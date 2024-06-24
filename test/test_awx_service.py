import json
import logging
import os
import os.path
import os.path
import re  # noqa: F401
import tempfile
import unittest
import urllib
from time import sleep

import yaml

from service.awx_service import AWXService
from service.tosca_helper import ToscaHelper

sure_tosca_base_url = 'http://localhost:8081/tosca-sure/1.0.0'
awx_base_url = 'https://localhost:8052/api/v2'
awx_username = 'admin'
awx_password = 'password'
logger = logging.getLogger(__name__)

class TestAWXService(unittest.TestCase):

    def awx(self,tosca_template_path=None, tosca_template_dict=None):
        try:
            tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
            logger.info('Deploying using awx.')

            if tosca_service_is_up:
                tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
                node_templates = tosca_template_dict['topology_template']['node_templates']

                awx = AWXService(api_url=awx_base_url, username=awx_username, password=awx_password,
                                 tosca_helper=tosca_helper)
                organization_id = awx.create_organization('sdia')
                topology_template_workflow_steps = {}
                for tosca_node_name in node_templates:
                    tosca_node = node_templates[tosca_node_name]
                    logger.info('Resolving function values for: ' + tosca_node_name)
                    tosca_node = tosca_helper.resolve_function_values(tosca_node)

                    credential = None
                    if 'attributes' in tosca_node:
                        if 'credential' in tosca_node['attributes']:
                            credential = tosca_node['attributes']['credential']
                        if 'user_key_pair' in tosca_node['attributes']:
                            credential = tosca_node['attributes']['user_key_pair']
                    if 'properties' in tosca_node:
                        if 'credential' in tosca_node['properties']:
                            credential = tosca_node['properties']['credential']
                        if 'user_key_pair' in tosca_node['properties']:
                            credential = tosca_node['properties']['user_key_pair']

                    logger.info('Creating workflow steps for: ' + tosca_node_name)
                    node_workflow_steps = awx.create_workflow_templates(tosca_node, organization_id=organization_id,
                                                                        credential=credential)
                    topology_template_workflow_steps.update(node_workflow_steps)

                workflows = tosca_helper.get_workflows()
                if workflows:
                    tosca_template_dict = self.execute_workflows(workflows=workflows,
                                                            topology_template_workflow_steps=topology_template_workflow_steps,
                                                            awx=awx)
        except (Exception) as ex:
            print(type(ex), ex)
            tosca_template_dict = str(ex)
            self.fail(ex)

        response = {'toscaTemplate': tosca_template_dict}
        logger.info("Returning Deployment")
        logger.info("Output message:" + json.dumps(response))
        return json.dumps(response)

    def execute_workflows(self,workflows=None, topology_template_workflow_steps=None, awx=None):
        launched_ids = []
        attributes = {}
        tosca_template_dict = {}
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
                                               workflow_name=workflow_name)
            logger.info('Added nodes to workflow')
            for wf_id in wf_ids:
                wf_job_ids = awx.launch(wf_id)
                logger.info('Launch workflows: ' + str(wf_job_ids))
                launched_ids += wf_job_ids
            for launched_id in launched_ids:
                while awx.get_job_status(launched_id) == 'running':
                    logger.info('Workflow: ' + str(launched_id) + ' status: ' + awx.get_job_status(launched_id))
                    sleep(5)
                job_id = awx.get_attribute_job_id(launched_id)
                if not job_id:
                    raise Exception('Could not find attribute job id from workflow: ' + str(launched_id))

                attributes.update(awx.get_job_artefacts(job_id))
                logger.info('Updated attributes:' + str(attributes))

            tosca_template_dict = awx.set_tosca_node_attributes(tosca_template_dict, attributes)
        return tosca_template_dict


    def test(self):
        # parsed_json_message = self.get_request_message_from_url('https://raw.githubusercontent.com/qcdis-sdia/sdia-deployer/develop/sample_requests/provision_request_workflow.json')
        # parsed_json_message = self.get_request_message_from_file('../sample_requests/provision_request_workflow.json')
        # owner = parsed_json_message['owner']
        # tosca_file_name = 'tosca_template'
        # tosca_template_dict = parsed_json_message['toscaTemplate']
        tosca_file_path = '../../sdia-tosca/examples/workflows_with_attributes.yaml'
        if os.path.isfile(tosca_file_path):
            tosca_template_dict = ToscaHelper.get_tosca_from_file(tosca_file_path)
        else:
            tosca_template_dict = self.get_tosca_from_url('https://raw.githubusercontent.com/qcdis-sdia/sdia-tosca/develop/examples/workflows_with_attributes.yaml')

        tmp_path = tempfile.mkdtemp()
        tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
        with open(tosca_template_path, 'w') as outfile:
            yaml.dump(tosca_template_dict, outfile, default_flow_style=False)

        self.awx(tosca_template_path=tosca_template_path, tosca_template_dict=tosca_template_dict)

    def get_request_message_from_url(self, url):
        with urllib.request.urlopen(
                url) as stream:
            parsed_json_message = json.load(stream)
        return parsed_json_message

    def get_tosca_from_url(self, url):
        with urllib.request.urlopen(
                url) as stream:
            parsed_json_message = yaml.load(stream)
        return parsed_json_message

    def get_request_message_from_file(self, path):
        with open(path) as json_file:
            return json.load(json_file)

    def get_tosca_template_path(self, parsed_json_message):
        tosca_file_name = 'tosca_template'
        tosca_template_dict = parsed_json_message['toscaTemplate']
        tmp_path = tempfile.mkdtemp()
        tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
        with open(tosca_template_path, 'w') as outfile:
            yaml.dump(tosca_template_dict, outfile, default_flow_style=False)
        return tosca_template_path


if __name__ == '__main__':
    unittest.main()
