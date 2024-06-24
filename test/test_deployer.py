import configparser
import json
import logging
import os
import os.path
import tempfile
import time
import traceback
import unittest
import urllib

import yaml

from service.awx_service import AWXService
from service.deploy_service import DeployService
from service.tosca_helper import ToscaHelper, set_node

sure_tosca_base_url = 'http://localhost:8081/tosca-sure/1.0.0'
semaphore_base_url = 'http://localhost:3000/api'
semaphore_password = 'password'
semaphore_username = 'admin'
awx_base_url = 'http://localhost:8052/api/v2'
awx_username = 'admin'
awx_password = 'password'
logger = logging.getLogger(__name__)
from cryptography.fernet import Fernet


class TestDeployer(unittest.TestCase):

    def test_decode(self):
        config = configparser.ConfigParser()
        path = os.getcwd()
        print("Current Directory", path)

        conf_path = '../properties.ini'
        try:
            f = open(conf_path)
        except IOError:
            conf_path = 'properties.ini'
            f = open(conf_path)
        finally:
            f.close()
        config.read(conf_path)
        secret = config['credential']['secret']
        key = bytes(secret, 'utf-8')

        fernet = Fernet(key)
        contents = 'SOM3.DATA_that_Need_ENCRIPTION8.'
        enc_message = fernet.encrypt(contents.encode())
        dec_message = fernet.decrypt(enc_message).decode()
        self.assertEqual(contents, dec_message)

    def test_inventory(self):
        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        semaphore_is_up = ToscaHelper.service_is_up(semaphore_base_url)
        if tosca_service_is_up and semaphore_is_up:
            parsed_json_message = self.get_request_message(
                'https://raw.githubusercontent.com/qcdis-sdia/sdia-deployer/master/sample_requests/deploy_request_mog.json')
            tosca_template_path = self.get_tosca_template_path(parsed_json_message)
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            nodes_pairs = tosca_helper.get_deployment_node_pipeline()
            vms = tosca_helper.get_vms()

    def test_deploy_service(self):
        parsed_json_message = self.get_request_message(
            'https://raw.githubusercontent.com/qcdis-sdia/sdia-deployer/master/sample_requests/deploy_request_mog.json')
        tosca_template_path = self.get_tosca_template_path(parsed_json_message)
        # owner = parsed_json_message['owner']

        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        semaphore_is_up = ToscaHelper.service_is_up(semaphore_base_url)

        if tosca_service_is_up and semaphore_is_up:
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            self.assertIsNotNone(tosca_helper.doc_id)
            nodes_to_deploy = tosca_helper.get_application_nodes()
            self.assertIsNotNone(nodes_to_deploy)
            nodes_pairs = tosca_helper.get_deployment_node_pipeline()
            self.assertIsNotNone(nodes_pairs)

            username = 'admin'
            deployService = DeployService(semaphore_base_url=semaphore_base_url,
                                          semaphore_username=username, semaphore_password='password',
                                          vms=tosca_helper.get_vms())
            for node_pair in nodes_pairs:
                deployService.deploy(node_pair)

    def test_deployer(self):

        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        semaphore_is_up = ToscaHelper.service_is_up(semaphore_base_url)

        if tosca_service_is_up and semaphore_is_up:

            f = open('../sample_requests/deploy_request_mog.json', )
            parsed_json_message = json.load(f)
            tosca_template_path = self.get_tosca_template_path(parsed_json_message)
            tosca_template_dict = parsed_json_message['toscaTemplate']
            if 'workflows' in tosca_template_dict['topology_template']:
                return self.awx(tosca_template_dict=tosca_template_dict, tosca_template_path=tosca_template_path)

            return self.semaphore(tosca_template_dict=tosca_template_dict, tosca_template_path=tosca_template_path)

    def get_request_message(self, url):
        logger = logging.getLogger(__name__)
        with urllib.request.urlopen(
                url) as stream:
            parsed_json_message = json.load(stream)
        return parsed_json_message

    def get_tosca_template_path(self, parsed_json_message):
        tosca_file_name = 'tosca_template'
        tosca_template_dict = parsed_json_message['toscaTemplate']

        tmp_path = tempfile.mkdtemp()
        tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
        with open(tosca_template_path, 'w') as outfile:
            yaml.dump(tosca_template_dict, outfile, default_flow_style=False)
        return tosca_template_path

    def semaphore(self, tosca_template_path=None, tosca_template_dict=None):
        tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
        nodes = tosca_helper.get_application_nodes()
        # nodes = tosca_helper.get_deployment_node_pipeline()

        deployService = DeployService(semaphore_base_url=semaphore_base_url, semaphore_username=semaphore_username,
                                      semaphore_password=semaphore_password, vms=tosca_helper.get_vms())
        try:
            for node in nodes:
                updated_node = deployService.deploy(node)
                if isinstance(updated_node, list):
                    for node in updated_node:
                        tosca_template_dict = set_node(node, tosca_template_dict)
                        # logger.info("tosca_template_dict :" + json.dumps(tosca_template_dict))
                else:
                    tosca_template_dict = set_node(updated_node, tosca_template_dict)
                    # logger.info("tosca_template_dict :" + json.dumps(tosca_template_dict))

            response = {'toscaTemplate': tosca_template_dict}
            output_current_milli_time = int(round(time.time() * 1000))
            response["creationDate"] = output_current_milli_time
            logger.info("Returning Deployment")
            logger.info("Output message:" + json.dumps(response))
            return json.dumps(response)
        except Exception as e:
            track = traceback.format_exc()
            print(track)
            raise

    def awx(self, tosca_template_path=None, tosca_template_dict=None, current_time=None):
        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        if tosca_service_is_up:
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            node_templates = tosca_template_dict['topology_template']['node_templates']
            awx = AWXService(api_url=awx_base_url, username=awx_username, password=awx_password)
            topology_template_workflow_steps = {}
            for tosca_node_name in node_templates:
                tosca_node = node_templates[tosca_node_name]
                logger.info('Resolving function values for: ' + tosca_node_name)
                tosca_node = tosca_helper.resolve_function_values(tosca_node)
                logger.info('Creating workflow steps for: ' + tosca_node_name)
                node_workflow_steps = awx.create_workflow_templates(tosca_node)
                topology_template_workflow_steps.update(node_workflow_steps)
            workflows = tosca_helper.get_workflows()
            if workflows:
                launched_ids = []
                for workflow_name in workflows:
                    workflow = workflows[workflow_name]
                    description = None
                    if 'description' in workflow:
                        description = workflow['description']
                    wf_ids = awx.create_workflow(description=description, workflow_name=workflow_name)
                    logger.info('Created workflow with ID: ' + str(wf_ids[0]))
                    workflow_node_ids = awx.create_dag(workflow_id=wf_ids[0],
                                                       tosca_workflow=workflow,
                                                       topology_template_workflow_steps=topology_template_workflow_steps,
                                                       workflow_name=workflow_name, current_time=current_time)
                    logger.info('Added nodes to workflow')
                    for wf_id in wf_ids:
                        wf_job_ids = awx.launch(wf_id)
                        launched_ids += wf_job_ids


if __name__ == '__main__':
    import unittest

    unittest.main()
