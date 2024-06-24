# To change this license header, choose License Headers in Project Properties.
# To change this template file, choose Tools | Templates
# and open the template in the editor.
import configparser
import json
import logging
import os
import sys
import tempfile
import traceback
from threading import Thread
from time import sleep
import datetime
import pika
import yaml
from concurrent.futures import as_completed, ThreadPoolExecutor
from cryptography.fernet import Fernet

from service.awx_service import AWXService
from service.tosca_helper import ToscaHelper, check_workflow_preconditions

logger = logging.getLogger(__name__)

done = False


def init_channel(rabbitmq_host_inst, queue_name_param):
    connection_inst = pika.BlockingConnection(pika.ConnectionParameters(host=rabbitmq_host_inst))
    channel_inst = connection_inst.channel()
    channel_inst.queue_declare(queue=queue_name_param)
    return channel_inst, connection_inst


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
    ch.queue_declare(queue='status-'+props.reply_to)


def save_tosca_template(tosca_template_dict):
    tmp_path = tempfile.mkdtemp()
    tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
    logger.info('Saving tosca_template at: ' + tosca_template_path)
    with open(tosca_template_path, 'w') as outfile:
        yaml.dump(tosca_template_dict, outfile, default_flow_style=False)
    return tosca_template_path


def execute_workflows(workflow=None, workflow_name=None, topology_template_workflow_steps=None, awx_inst=None,
                      tosca_template_dict=None,
                      current_time=None):
    launched_ids = []
    attributes = {}
    launched_workflow = {'name': workflow_name}
    description = None
    if 'description' in workflow:
        description = workflow['description']
    logger.info('Creating workflow: ' + str(workflow_name))
    wf_ids = awx_inst.create_workflow(description=description, workflow_name=workflow_name)
    logger.info('Created workflow with name:' + workflow_name + ', ID: ' + str(wf_ids[0]))

    workflow_node_ids = awx_inst.create_dag(workflow_id=wf_ids[0],
                                            tosca_workflow=workflow,
                                            topology_template_workflow_steps=topology_template_workflow_steps,
                                            workflow_name=workflow_name,
                                            current_time=current_time)

    logger.info('Added nodes to workflow')
    for wf_id in wf_ids:
        wf_job_ids = awx_inst.launch(wf_id)
        logger.info('Launch workflows: ' + str(wf_job_ids))
        launched_ids += wf_job_ids
    for launched_id in launched_ids:
        logger.info('Workflow: ' + str(launched_id) + ' status: ' + awx_inst.get_workflow_status(launched_id))
        while awx_inst.get_workflow_status(launched_id) == 'running' or \
                awx_inst.get_workflow_status(launched_id) == 'pending' or \
                awx_inst.get_workflow_status(launched_id) == 'waiting':
            logger.info('Workflow: ' + str(launched_id) + ' status: ' + awx_inst.get_workflow_status(launched_id))
            workflow_nodes = awx_inst.get_workflow_nodes(launched_id)
            for workflow_node in workflow_nodes:
                if 'job' in workflow_node['summary_fields']:
                    job = workflow_node['summary_fields']['job']
                    tosca_template_dict = tosca_helper.set_node_state(tosca_template_dict=tosca_template_dict,
                                                                      job=job, workflow_name=workflow_name,
                                                                      current_time=current_time)
                    # update_tosca_template(tosca_template_dict)
            sleep(10)
        wf_status = awx_inst.get_workflow_status(launched_id)
        logger.info('Workflow status: ' + str(wf_status))
        workflow_nodes = awx_inst.get_workflow_nodes(launched_id)
        if 'failed' == wf_status:
            tosca_template_dict = tosca_helper.set_node_state(tosca_template_dict=tosca_template_dict, job=job,
                                                              workflow_name=workflow_name,
                                                              current_time=current_time)
            # update_tosca_template(tosca_template_dict)
            failed_nodes = []
            for node in workflow_nodes:
                if 'summary_fields' in node and 'job' in node['summary_fields'] and node['summary_fields']['job']['status'] == 'failed':
                    failed_nodes.append(node['summary_fields']['job']['name'])
            if failed_nodes:
                raise Exception('Workflow execution failed. Failed nodes: '+str(failed_nodes))
            else:
                raise Exception('Workflow execution failed.')

        for workflow_node in workflow_nodes:
            if 'job' in workflow_node['summary_fields']:
                job = workflow_node['summary_fields']['job']
                tosca_template_dict = tosca_helper.set_node_state(tosca_template_dict=tosca_template_dict, job=job,
                                                                  workflow_name=workflow_name,
                                                                  current_time=current_time)
                # update_tosca_template(tosca_template_dict)
        attributes_job_ids = awx_inst.get_attributes_job_ids(launched_id)
        if not attributes_job_ids:
            raise Exception('Could not find attribute job id from workflow: ' + str(launched_id))
        for job_id in attributes_job_ids:
            attributes.update(awx_inst.get_job_artifacts(job_id))
    tosca_template_dict = awx_inst.set_tosca_node_attributes(tosca_template_dict, attributes)

    return tosca_template_dict

def create_workflow_templates(tosca_workflow_step=None,
                                organization_id=None,
                                node_templates=None,
                                step_name=None,
                                workflow_name=None,
                                num_of_forks=None):
    pass

def awx(tosca_template_path=None, tosca_template_dict=None):
    awx_inst = None
    global tosca_helper
    current_time = datetime.datetime.now()
    try:
        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        if tosca_service_is_up:
            logger.info('Initializing ToscaHelper')
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            node_templates = tosca_template_dict['topology_template']['node_templates']
            logger.info('Initializing AWXService')
            awx_inst = AWXService(api_url=awx_base_url, username=awx_username, password=awx_password,
                                  tosca_helper=tosca_helper)
            logger.info('Creating organization: sdia')
            organization_id = awx_inst.create_organization('sdia')

            for tosca_node_name in node_templates:
                tosca_node = node_templates[tosca_node_name]
                logger.info('Resolving function values for: ' + tosca_node_name)
                tosca_node = tosca_helper.resolve_function_values(tosca_node)

            workflows = tosca_helper.get_workflows()
            if workflows:
                launched_workflows = []
                for workflow_name in workflows:
                    topology_template_workflow_steps = {}
                    futures = {}
                    workflow = workflows[workflow_name]
                    can_run = check_workflow_preconditions(workflow, tosca_template_dict)
                    logger.info('workflow: ' + workflow_name + ' can run: ' + str(can_run))
                    if can_run:
                        steps = workflow['steps']
                        # with ThreadPoolExecutor(max_workers=10) as executor:
                        #     for step_name in steps:
                        #         futures.update(executor.submit(create_workflow_templates, tosca_workflow_step=steps[step_name],
                        #         organization_id=organization_id,
                        #         node_templates=node_templates,
                        #         step_name=step_name + '_' + str(current_time),
                        #         workflow_name=workflow_name + '_' + str(current_time),
                        #         num_of_forks=100))
                        for step_name in steps:
                            logger.info('Created step_name: ' + str(step_name))
                            node_workflow_steps = awx_inst.create_workflow_templates(
                                tosca_workflow_step=steps[step_name],
                                organization_id=organization_id,
                                node_templates=node_templates,
                                step_name=step_name + '_' + str(current_time),
                                workflow_name=workflow_name + '_' + str(current_time),
                                num_of_forks=100)
                            topology_template_workflow_steps.update(node_workflow_steps)

                        tosca_template_dict = execute_workflows(workflow=workflow,
                                                                workflow_name=workflow_name + '_' + str(current_time),
                                                                topology_template_workflow_steps=topology_template_workflow_steps,
                                                                awx_inst=awx_inst,
                                                                tosca_template_dict=tosca_template_dict,
                                                                current_time=current_time)
        else:
            raise Exception('Could not connect to sure tosca at ' + sure_tosca_base_url)
    except Exception as ex:
        track = traceback.format_exc()
        print(track)
        raise
    finally:
        if awx_inst and delete_templates_after_execution:
            awx_inst.clean_up_execution()
    tosca_template_dict = encrypt_credentials(tosca_template_dict)
    response = {'toscaTemplate': tosca_template_dict}
    logger.info("Returning Deployment")
    # logger.info("Output message:" + json.dumps(response))
    return json.dumps(response)


def decrypt_credentials(tosca_template_dict):
    logger.info('Decrypting credentials.')
    node_templates = tosca_template_dict['topology_template']['node_templates']
    enc_key = bytes(secret, 'utf-8')
    for node_template_name in node_templates:
        node_template = node_templates[node_template_name]
        if node_template['type'] == 'tosca.nodes.QC.VM.Compute':
            continue
        credentials = ToscaHelper.extract_credentials_from_node(node_template)

        if credentials:
            for credential in credentials:
                if 'protocol' in credential and credential['protocol'] == 'ssh':
                    continue
                if 'token' in credential:
                    token = credential['token']
                    credential['token'] = decrypt(token, enc_key)
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
        credentials = ToscaHelper.extract_credentials_from_node(node_template)
        if credentials:
            for credential in credentials:
                if 'get_attribute' in credential or 'get_property' in credential:
                    continue
                if 'token' not in credential:
                    # This is a tmp fix for the tosca parser. The tosca.datatypes.Credential which requires token
                    credential['token'] = 'dG9rZW4K'
                if 'protocol' in credential and credential['protocol'] == 'ssh':
                    continue
                if 'token' in credential:
                    token = credential['token']
                    credential['token'] = encrypt(token, enc_key)
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


def handle_exception(ex=None, workflow_name=None, tosca_template=None):
    error_report = str(ex)
    return tosca_template, error_report


def handle_delivery(message):
    logger.info("Got new message")
    try:
        message = message.decode()
    except (UnicodeDecodeError, AttributeError):
        pass
    parsed_json_message = json.loads(message)
    owner = parsed_json_message['owner']
    tosca_file_name = 'tosca_template'
    tosca_template_dict = parsed_json_message['toscaTemplate']
    tosca_template_dict = decrypt_credentials(tosca_template_dict)

    tosca_template_path = save_tosca_template(tosca_template_dict)
    # if 'workflows' in tosca_template_dict['topology_template']:
    try:
        return awx(tosca_template_dict=tosca_template_dict, tosca_template_path=tosca_template_path)
    except Exception as ex:
        tosca_template_dict, error_report = handle_exception(tosca_template=tosca_template_dict, ex=ex)
        # tosca_template_dict['error'] = str(ex)
        response = {'toscaTemplate': tosca_template_dict, 'errorReport': error_report}
        logger.info("Output message:" + json.dumps(response))
        return json.dumps(response)


def threaded_function(args):
    while not done:
        connection.process_data_events()
        sleep(8)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    global channel, queue_name, connection, rabbitmq_host, sure_tosca_base_url, \
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

    delete_templates_after_execution = config['sdia-deployer']['delete_templates_after_execution'].lower() in (
        "yes", "true", "t", "1")

    logger.info(
        'Properties sure_tosca_base_url: ' + sure_tosca_base_url + ', rabbitmq_host: ' + rabbitmq_host + ', '
                                                                                                         'queue_name:'
                                                                                                         ' ' + queue_name)

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
