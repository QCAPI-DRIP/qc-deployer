# To change this license header, choose License Headers in Project Properties.
# To change this template file, choose Tools | Templates
# and open the template in the editor.
import base64
import configparser
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from collections import deque
from threading import Thread
from time import sleep

import pika
import yaml

from cryptography.fernet import Fernet

from service.awx_service import AWXService
from service.deploy_service import DeployService
from service.tosca_helper import ToscaHelper

logger = logging.getLogger(__name__)

done = False

def init_channel(rabbitmq_host, queue_name):
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host))
    channel = connection.channel()
    channel.queue_declare(queue=queue_name)
    return channel, connection


def start(this_channel):
    try:
        this_channel.basic_qos(prefetch_count=1)
        this_channel.basic_consume(queue=queue_name, on_message_callback=on_request)
        logger.info(" [x] Awaiting RPC requests")
        this_channel.start_consuming()
    except:
        exit(-1)


def on_request(ch, method, props, body):
    response = handle_delivery(body)

    ch.basic_publish(exchange='',
                     routing_key=props.reply_to,
                     properties=pika.BasicProperties(correlation_id=
                                                     props.correlation_id),
                     body=str(response))
    ch.basic_ack(delivery_tag=method.delivery_tag)



def save_tosca_template(tosca_template_dict):
    tmp_path = tempfile.mkdtemp()
    tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
    logger.info('Saving tosca_template at: '+tosca_template_path)
    with open(tosca_template_path, 'w') as outfile:
        yaml.dump(tosca_template_dict, outfile, default_flow_style=False)
    return  tosca_template_path

def execute_workflows(workflow=None, workflow_name=None,topology_template_workflow_steps=None, awx=None,tosca_template_dict=None):
    launched_ids = []
    attributes = {}
    description = None
    if 'description' in workflow:
        description = workflow['description']
    wf_ids = awx.create_workflow(description=description, workflow_name=workflow_name)
    logger.info('Created workflow with name:'+ workflow_name +', ID: ' + str(wf_ids[0]))
    workflow_node_ids = awx.create_dag(workflow_id=wf_ids[0],
                                       tosca_workflow=workflow,
                                       topology_template_workflow_steps=topology_template_workflow_steps)
    logger.info('Added nodes to workflow')
    for wf_id in wf_ids:
        wf_job_ids = awx.launch(wf_id)
        logger.info('Launch workflows: ' + str(wf_job_ids))
        launched_ids += wf_job_ids


    for launched_id in launched_ids:
        while awx.get_workflow_status(launched_id) == 'running':
            logger.info('Workflow: ' + str(launched_id) + ' status: ' + awx.get_workflow_status(launched_id))
            workflow_nodes = awx.get_workflow_nodes(launched_id)
            for workflow_node in workflow_nodes:
                if 'job' in workflow_node['summary_fields']:
                    job =workflow_node['summary_fields']['job']
                    tosca_template_dict = tosca_helper.set_node_state(tosca_template_dict=tosca_template_dict,job=job,workflow_name=workflow_name)
            sleep(8)
        job_status = awx.get_workflow_status(launched_id)
        if 'failed' == job_status:
            raise Exception('Workflow execution failed')

        workflow_nodes = awx.get_workflow_nodes(launched_id)
        for workflow_node in workflow_nodes:
            if 'job' in workflow_node['summary_fields']:
                job =workflow_node['summary_fields']['job']
                tosca_template_dict = tosca_helper.set_node_state(tosca_template_dict=tosca_template_dict,job=job,workflow_name=workflow_name)
        attributes_job_ids = awx.get_attributes_job_ids(launched_id)
        if not attributes_job_ids:
            raise Exception('Could not find attribute job id from workflow: ' + str(launched_id))
        for job_id in attributes_job_ids:
            attributes.update(awx.get_job_artifacts(job_id))
    tosca_template_dict = awx.set_tosca_node_attributes(tosca_template_dict, attributes)

    return tosca_template_dict


def extract_credentials_from_node(tosca_node):
    credentials = []
    for name in ['attributes', 'properties']:
        if name in tosca_node:
            # for cred_name in ['credential', 'credentials', 'user_key_pair']:
            for cred_name in ['credential', 'credentials']:
                if cred_name in tosca_node[name]:
                    credential = tosca_node[name][cred_name]
                    if isinstance(credential, list):
                        credentials.extend(credential)
                    else:
                        credentials.append(credential)
    return credentials


def awx(tosca_template_path=None, tosca_template_dict=None):
    awx = None
    global tosca_helper
    try:
        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        if tosca_service_is_up:
            logger.info('Initializing ToscaHelper')
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            node_templates = tosca_template_dict['topology_template']['node_templates']
            logger.info('Initializing AWXService')
            awx = AWXService(api_url=awx_base_url, username=awx_username, password=awx_password,
                             tosca_helper=tosca_helper)
            logger.info('Creating organization: sdia')
            organization_id = awx.create_organization('sdia')
            topology_template_workflow_steps = {}
            for tosca_node_name in node_templates:
                tosca_node = node_templates[tosca_node_name]
                logger.info('Resolving function values for: ' + tosca_node_name)
                tosca_node = tosca_helper.resolve_function_values(tosca_node)

                credentials = extract_credentials_from_node(tosca_node)
                logger.info('Creating workflow steps for: ' + tosca_node_name)
                node_workflow_steps = awx.create_workflow_templates(tosca_node, organization_id=organization_id,
                                                                    credentials=credentials, tosca_node_name=tosca_node_name)
                topology_template_workflow_steps.update(node_workflow_steps)

            workflows = tosca_helper.get_workflows()
            if workflows:
                for workflow_name in workflows:
                    workflow = workflows[workflow_name]
                    can_run = tosca_helper.check_workflow_preconditions(workflow,tosca_template_dict)
                    if can_run:
                        tosca_template_dict = execute_workflows(workflow=workflow,workflow_name=workflow_name,
                                                                     topology_template_workflow_steps=topology_template_workflow_steps,
                                                                     awx=awx,
                                                                    tosca_template_dict=tosca_template_dict)
        else:
            raise Exception('Could not connect to sure tosca at '+sure_tosca_base_url)
    except (Exception) as ex:
        track = traceback.format_exc()
        print(track)
        raise
    finally:
        if awx and delete_templates_after_execution:
            awx.clean_up_execution()
    tosca_template_dict = encrypt_credentials(tosca_template_dict)
    response = {'toscaTemplate': tosca_template_dict}
    logger.info("Returning Deployment")
    logger.info("Output message:" + json.dumps(response))
    return json.dumps(response)

def decrypt_credentials(tosca_template_dict):
    logger.info('Decrypting credentials.')
    node_templates = tosca_template_dict['topology_template']['node_templates']
    enc_key = bytes(secret, 'utf-8')
    for node_template_name in node_templates:
        node_template = node_templates[node_template_name]
        credentials = extract_credentials_from_node(node_template)
        if credentials:
            for credential in credentials:
                if 'token' in credential:
                    token = credential['token']
                    credential['token'] = decrypt(token,enc_key)
                if 'keys' in credential:
                    keys = credential['keys']
                    for key_name in keys:
                        token = keys[key_name]
                        keys[key_name] = decrypt(token, enc_key)
    return tosca_template_dict


def encrypt_credentials(tosca_template_dict):
    logger.info('Encrypting credentials.')
    node_templates = tosca_template_dict['topology_template']['node_templates']
    enc_key = bytes(secret, 'utf-8')
    for node_template_name in node_templates:
        node_template = node_templates[node_template_name]
        credentials = extract_credentials_from_node(node_template)
        if credentials:
            for credential in credentials:
                if 'token' in credential:
                    token = credential['token']
                    credential['token'] = encrypt(token,enc_key)
                if 'keys' in credential:
                    keys = credential['keys']
                    for key_name in keys:
                        token = keys[key_name]
                        keys[key_name] = encrypt(token, enc_key)
    return tosca_template_dict


def encrypt(contents, key):
    try:
        fernet = Fernet(key)
        dec = fernet.encrypt(contents.encode())
        return dec.decode()
    except Exception as ex:
        done = True
        e = sys.exc_info()[0]
        logger.info("Error: " + str(e))
        print(e)
        exit(-1)


def decrypt(contents, key):
    try:
        fernet = Fernet(key)
        dec = fernet.decrypt(contents.encode()).decode()
        return dec
    except Exception as ex:
        done = True
        e = sys.exc_info()[0]
        logger.info("Error: " + str(e))
        print(e)
        exit(-1)





def handle_delivery(message):
    logger.info("Got: " + str(message))
    try:
        message = message.decode()
    except (UnicodeDecodeError, AttributeError):
        pass
    parsed_json_message = json.loads(message)
    owner = parsed_json_message['owner']
    tosca_file_name = 'tosca_template'
    tosca_template_dict = parsed_json_message['toscaTemplate']
    tosca_template_dict =  decrypt_credentials(tosca_template_dict)

    tosca_template_path = save_tosca_template(tosca_template_dict)
    # if 'workflows' in tosca_template_dict['topology_template']:
    try:
        return awx(tosca_template_dict=tosca_template_dict, tosca_template_path=tosca_template_path)
    except (Exception) as ex:
        tosca_template_dict['error'] = str(ex)
        return tosca_template_dict

def threaded_function(args):
    while not done:
        connection.process_data_events()
        sleep(8)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    global channel, queue_name, connection, rabbitmq_host, sure_tosca_base_url,\
        awx_base_url, awx_username, awx_password, secret, delete_templates_after_execution

    config = configparser.ConfigParser()
    config.read('properties.ini')
    sure_tosca_base_url = config['tosca-sure']['base_url']

    awx_base_url = config['awx']['base_url']
    awx_username = config['awx']['username']
    awx_password = config['awx']['password']


    rabbitmq_host = config['message_broker']['host']
    queue_name = config['message_broker']['queue_name']

    secret = config['credential']['secret']

    delete_templates_after_execution = config['sdia-deployer']['delete_templates_after_execution'].lower() in ("yes", "true", "t", "1")

    logger.info('Properties sure_tosca_base_url: ' + sure_tosca_base_url + ', rabbitmq_host: ' + rabbitmq_host+ ', queue_name: '+queue_name)

    channel, connection = init_channel(rabbitmq_host, queue_name)
    logger.info("Awaiting RPC requests")
    try:
        thread = Thread(target=threaded_function, args=(1,))
        thread.start()
        start(channel)
    except Exception as e:
        done = True
        e = sys.exc_info()[0]
        logger.info("Error: " + str(e))
        print(e)
        exit(-1)
