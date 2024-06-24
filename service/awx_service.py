import base64
import json
import logging
import os
import random
import string
import tempfile
import time
import traceback
import uuid
from base64 import b64encode

import ansible.inventory.manager
import networkx as nx
import requests
import tower_cli.exceptions
import validators
import yaml
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader
from service.tosca_helper import ToscaHelper
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class AWXService:

    def __init__(self, api_url=None, username=None, password=None, tosca_helper=None):
        self.login(username=username, password=password, api_url=api_url)
        self.tosca_helper = tosca_helper

    def login(self, username=None, password=None, api_url=None, token=None):
        self._session = requests.Session()
        self.api_url = api_url
        self.headers = None
        if token:
            self.headers = {
                'Authorization': 'Bearer {0}'.format(token),
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        elif username and password:
            user_pass = '{0}:{1}'.format(username, password).encode()
            user_pass_encoded = b64encode(user_pass).decode()
            self.headers = {
                'Authorization': 'Basic {0}'.format(user_pass_encoded),
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        r = self._session.get(self.api_url + '/me/', headers=self.headers, verify=False)

        if r.status_code == 403 or r.status_code == 401:
            raise tower_cli.exceptions.AuthError

    def create_project(self, project_name=None, scm_url=None, scm_branch=None, credential=None, scm_type=None,
                       organization_id=None):

        body = {
            'name': project_name,
            'description': '',
            'scm_type': scm_type,
            'scm_url': scm_url,
            'scm_branch': scm_branch,
            'scm_refspec': '',
            'scm_clean': False,
            'scm_delete_on_update': False,
            'credential': credential,
            'timeout': 0,
            'organization': organization_id,
            'scm_update_on_launch': True,
            'scm_update_cache_timeout': 0,
            'allow_override': False
        }

        return self.post(body, 'projects')

    def create_inventory(self, inventory_name=None, inventory=None, organization_id=None):
        loader = DataLoader()
        fd, inventory_path = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as outfile:
            yaml.dump(inventory, outfile, default_flow_style=False)
        inventory_manager: InventoryManager = ansible.inventory.manager.InventoryManager(loader=loader,
                                                                                         sources=inventory_path)

        body = {
            'name': inventory_name,
            'description': 'delete_after_execution',
            'organization': organization_id,
            'kind': '',
            'host_filter': None,
            'variables': '',
            'insights_credential': None
        }
        inventory_ids = self.get_resources(api_path=
                                           'inventories/?name=' + inventory_name + '&organization=' + str(
                                               organization_id))
        if inventory_ids and inventory_ids[0]:
            r = self._session.delete(self.api_url + '/inventories/' + str(inventory_ids[0]['id']), headers=self.headers,
                                     verify=False)
        inventory_id = []
        index = 0
        while not inventory_id and index <= 10:
            inventory_id = self.post(body, 'inventories')
            index += 1

        logger.info('inventory name: ' + inventory_name + ' inventory_id: ' + str(inventory_id))
        inventory_id = inventory_id[0]
        for group_name in inventory_manager.groups:
            group = inventory_manager.groups[group_name]
            if group_name == 'all' or group_name == 'ungrouped':
                for host in group.hosts:
                    inventory_hosts_id = self.create_inventory_hosts(host, inventory_id=inventory_id)
            else:
                inventory_group_ids = self.create_inventory_group(group_name, group, inventory_id)
                for host in group.hosts:
                    inventory_hosts_id = self.create_inventory_hosts(host, inventory_group_id=inventory_group_ids[0])
        return inventory_id

    def create_workflow(self, description=None, workflow_name=None, topology_template_workflow_steps=None,
                        organization_id=None) -> list:
        description = ''
        body = {
            'name': workflow_name,
            'description': 'delete_after_execution',
            'organization': organization_id,
            'survey_enabled': False,
            'allow_simultaneous': True,
            'ask_variables_on_launch': False,
            'scm_branch': '',
            'ask_inventory_on_launch': False,
            'ask_scm_branch_on_launch': False,
            'ask_limit_on_launch': False,
            'webhook_service': None,
            'webhook_credential': None
        }
        return self.post(body, 'workflow_job_templates')

    def post(self, body, api_path):
        r = self._session.post(self.api_url + '/' + api_path + '/', data=json.dumps(body), headers=self.headers,
                               verify=False)
        ids = []
        if r.status_code == 201 or r.status_code == 202 or r.status_code == 204:
            if r.text:
                json_resp = r.json()
                if 'id' in json_resp:
                    id = json_resp['id']
                    ids.append(id)
            return ids
        elif r.status_code == 400 and 'already exists' in r.text:
            if 'name' in body:
                r = self._session.get(self.api_url + '/' + api_path + '/?name=' + body['name'], headers=self.headers,
                                      verify=False)
            elif 'identifier' in body:
                r = self._session.get(self.api_url + '/' + api_path + '/?identifier=' + body['identifier'],
                                      headers=self.headers, verify=False)
            results = r.json()['results']

            for res in results:
                ids.append(res['id'])
            return ids
        else:
            raise Exception('Response Code:' + str(r.status_code) + ' ' + r.text + '\nRequest Body: ' + str(body))

    def create_inventory_group(self, group_name, group, inventory_id):
        if 'inventory_file' in group.vars:
            group.vars.pop('inventory_file')
        if 'inventory_dir' in group.vars:
            group.vars.pop('inventory_dir')
        body = {
            'name': group_name,
            'description': 'delete_after_execution',
            'variables': json.dumps(group.vars)
        }
        self.delete('inventories', '?name=' + group_name)
        inventory_group_ids = self.post(body, 'inventories/' + str(inventory_id) + '/groups')
        return inventory_group_ids

    def create_inventory_hosts(self, host, inventory_id=None, inventory_group_id=None):
        if 'inventory_file' in host.vars:
            host.vars.pop('inventory_file')
        if 'inventory_dir' in host.vars:
            host.vars.pop('inventory_dir')
        inventory_hosts_ids = []
        if inventory_id:
            body = {
                'name': host.address,
                'description': 'delete_after_execution',
                'inventory': inventory_id,
                'enabled': True,
                'instance_id': '',
                'variables': json.dumps(host.vars)
            }
            try:
                inventory_hosts_ids = self.post(body, 'hosts')
            except (Exception) as ex:
                track = traceback.format_exc()
                print(track)
                raise ex

            return inventory_hosts_ids
        if inventory_group_id:
            body = {
                'name': host.address,
                'description': 'delete_after_execution',
                'enabled': True,
                'instance_id': '',
                'variables': json.dumps(host.vars)
            }
            self.delete('groups', '?name=' + host.address)
            inventory_hosts_ids = self.post(body, 'groups/' + str(inventory_group_id) + '/hosts')
        return inventory_hosts_ids

    def get_resources(self, api_path=None, results=None):
        r = self._session.get(self.api_url + '/' + api_path, headers=self.headers, verify=False)
        if r.status_code == 200:
            res_json = r.json()
            if 'results' in res_json:
                if not results:
                    results = []
                if res_json['results']:
                    results = results + res_json['results']
                if 'next' in res_json and res_json['next']:
                    path = res_json['next'].replace('/api/v2/', '')
                    results = results + self.get_resources(api_path=path, results=results)
                return results
            else:
                return res_json
        if r.status_code == 404:
            return None
        else:
            raise Exception(r.text)

    def create_job_template(self, operation=None, credentials=None, organization_id=None, extra_vars=None,
                            num_of_forks=None):
        operation_name = list(operation.keys())[0]
        body = {
            'name': operation_name,
            'description': 'delete_after_execution',
            'job_type': 'run',
            'inventory': operation[operation_name]['inventory'],
            'project': operation[operation_name]['project'],
            'playbook': operation[operation_name]['implementation'],
            'scm_branch': '',
            'forks': num_of_forks,
            'limit': '',
            'verbosity': 0,
            'extra_vars': json.dumps(extra_vars),
            'job_tags': '',
            'force_handlers': False,
            'skip_tags': '',
            'start_at_task': '',
            'timeout': 0,
            'use_fact_cache': False,
            'host_config_key': '',
            'ask_scm_branch_on_launch': False,
            'ask_diff_mode_on_launch': False,
            'ask_variables_on_launch': False,
            'ask_limit_on_launch': False,
            'ask_tags_on_launch': False,
            'ask_skip_tags_on_launch': False,
            'ask_job_type_on_launch': False,
            'ask_verbosity_on_launch': False,
            'ask_inventory_on_launch': False,
            'ask_credential_on_launch': False,
            'survey_enabled': False,
            'become_enabled': False,
            'diff_mode': False,
            'allow_simultaneous': False,
            'custom_virtualenv': None,
            'job_slice_count': 1,
            'webhook_service': None,
            'webhook_credential': None
        }

        fail_count = 0
        job_templates_ids = None
        while fail_count < 60:
            try:
                job_templates = \
                    self.get_resources(api_path=
                                       'job_templates/?name=' + operation_name + '&organization=' + str(
                                           organization_id))
                if job_templates:
                    job_template = job_templates[0]
                    try:
                        job_templates_ids = self.put(body, 'job_templates/' + str(job_template['id']))
                    except Exception as ex:
                        track = traceback.format_exc()
                        print(track)
                        raise ex
                else:
                    job_templates_ids = self.post(body, 'job_templates')
                if credentials:
                    # rnd_str = ''.join(random.choice(string.ascii_lowercase) for i in range(5))
                    for credential in credentials:
                        credential_ids = self.add_credentials(credential=credential,
                                                              path='job_templates/' + str(
                                                                  job_templates_ids[0]) + '/credentials',
                                                              organization_id=organization_id, name=operation_name)
                return job_templates_ids
            except Exception as ex:
                if 'Playbook not found for project' in str(ex):
                    fail_count += 1
                    time.sleep(6.5)
                    logger.warning(str(ex) + '. Retrying to update project fail_count: ' + str(fail_count))
                    update_ids = self.update_project(operation[operation_name]['project'])
                elif fail_count >= 60:
                    raise ex
                else:
                    raise ex
        return job_templates_ids

    def create_workflow_templates(self, tosca_workflow_step=None, organization_id=None,
                                  node_templates=None, step_name=None, workflow_name=None, num_of_forks=None):
        awx_workflow_steps = {}
        tosca_node = node_templates[tosca_workflow_step['target']]
        activities = tosca_workflow_step['activities']
        call_operation = None
        for activity in activities:
            if 'call_operation' in activity:
                call_operation = activity['call_operation']
                break

        if not call_operation:
            raise Exception('Workflow step: ' + step_name + ' has no call_operation')
        if 'interfaces' in tosca_node:
            interfaces = tosca_node['interfaces']
            interface_name = call_operation.split('.')[0]
            tosca_interface_job = call_operation.split('.')[1]
            ancestors = self.tosca_helper.get_interface_ancestors(interface_name)
            if 'tosca.interfaces.QC.Ansible' in ancestors:
                workflow_template_node = {}
                if tosca_interface_job not in interfaces[interface_name]:
                    raise Exception(
                        tosca_interface_job + ' is not in ' + interface_name + 'interface. Check input TOSCA '
                                                                               'of ' + interface_name + 'interface definition')
                template = interfaces[interface_name][tosca_interface_job]
                template_name = workflow_name + '.' + step_name
                logger.info('Creating template: ' + template_name)
                extra_variables = None
                if 'repository' not in template['inputs']:
                    raise Exception('Workflow steps for: ' + template_name + ' have no repository: ' + str(template))
                if 'inputs' in template and 'repository' in template['inputs']:
                    repository_url = template['inputs']['repository']
                    project_id = self.create_project(project_name=repository_url, scm_url=repository_url,
                                                     scm_branch='master', scm_type='git',
                                                     organization_id=organization_id)
                    workflow_template_node[template_name] = {'project': project_id[0]}
                    if not 'inventory' in template['inputs']:
                        raise Exception(tosca_interface_job + ' has no inventory')
                    inventory = template['inputs']['inventory']
                    inventory_id = self.create_inventory(inventory_name=template_name, inventory=inventory,
                                                         organization_id=organization_id)
                    workflow_template_node[template_name]['inventory'] = inventory_id
                    logger.info('Created inventory: ' + str(inventory_id) + ' for :' + template_name)
                if 'implementation' in template:
                    if 'extra_variables' in template['inputs']:
                        extra_variables = self.get_variables(extra_variables=template['inputs']['extra_variables'])
                    workflow_template_node[template_name]['implementation'] = template['implementation']
                    if not workflow_template_node[template_name]['inventory']:
                        raise Exception(template_name + ' is missing inventory')
                    credentials = ToscaHelper.extract_credentials_from_node(tosca_node)
                    workflow_template_node[template_name]['job_template'] = \
                        self.create_job_template(workflow_template_node,
                                                 credentials=credentials,
                                                 organization_id=organization_id,
                                                 extra_vars=extra_variables,
                                                 num_of_forks=num_of_forks)[0]
                else:
                    raise Exception(template_name + ' has no implementation!')
                if workflow_template_node:
                    logger.info(
                        'Created workflow_template_node: ' + str(workflow_template_node) + ' number of forks: ' + str(
                            num_of_forks))
                    awx_workflow_steps.update(workflow_template_node)
        return awx_workflow_steps

    def update_project(self, project_id):
        body = {}
        resp = self.post(body, 'projects/' + str(project_id) + '/update')
        return resp

    def create_on_success_node(self, parent_node_id, success_job_template_id):
        body = {
            'extra_data': {},
            'inventory': None,
            'scm_branch': '',
            'job_type': None,
            'job_tags': '',
            'skip_tags': '',
            'limit': '',
            'diff_mode': None,
            'verbosity': None,
            'unified_job_template': str(success_job_template_id),
            'all_parents_must_converge': None,
            'identifier': str(uuid.uuid1())
        }
        success_node_ids = self.post(body, 'workflow_job_template_nodes/' + str(parent_node_id) + '/success_nodes')
        return success_node_ids

    def create_dag(self, workflow_id=None, tosca_workflow=None, topology_template_workflow_steps=None,
                   workflow_name=None, current_time=None):
        # Don't look at this you face will melt
        graph = nx.DiGraph()
        steps = tosca_workflow['steps']
        for step_name in steps:
            graph.add_node(step_name)
            logger.info('Creating step: ' + step_name)
            step = steps[step_name]
            activities = step['activities']
            for activity in activities:
                if 'on_success' in activity:
                    graph = self.add_edge(graph=graph, parent_name=step_name, children=activity['on_success'],
                                          label='on_success')
                if 'on_failure' in activity:
                    graph = self.add_edge(graph=graph, parent_name=step_name, children=activity['on_failure'],
                                          label='on_failure')

        for step_name in graph:
            if graph.in_degree(step_name) == 0:
                step = steps[step_name]
                activities = step['activities']
                # target = step['target']
                for activity in activities:
                    parent_node_ids = []
                    if 'call_operation' in activity:
                        # call_operation = activity['call_operation']
                        template_name = workflow_name + '.' + step_name + '_' + str(current_time)

                        parent_node_ids.append(self.create_root_workflow_node(workflow_id=workflow_id,
                                                                              job_template_id=
                                                                              topology_template_workflow_steps[
                                                                                  template_name]['job_template'],
                                                                              step_name=step_name)[0])
                    for parent_node in parent_node_ids:
                        for outcome in ['on_failure', 'on_success']:
                            if outcome in activity:
                                node_children = activity[outcome]
                                children = []
                                if isinstance(node_children, str):
                                    children.append(node_children)
                                elif isinstance(node_children, list):
                                    children = node_children
                                for child in children:
                                    self.create_workflow_nodes(parent_id=parent_node,
                                                               child=child,
                                                               label=outcome,
                                                               steps=steps,
                                                               topology_template_workflow_steps=topology_template_workflow_steps,
                                                               workflow_id=workflow_id, workflow_name=workflow_name,
                                                               current_time=current_time)
        return None

    def add_edge(self, graph=None, parent_name=None, children=None, label=None):
        if isinstance(children, list):
            for child in children:
                self.add_edge(graph=graph, parent_name=parent_name, children=child, label=label)
        elif isinstance(children, str):
            child = children
            graph.add_edge(parent_name, child, label=label)
        return graph

    def create_root_workflow_node(self, workflow_id, job_template_id, step_name):
        body = {
            'extra_data': {},
            'inventory': None,
            'scm_branch': None,
            'job_type': None,
            'job_tags': '',
            'skip_tags': None,
            'limit': None,
            'diff_mode': None,
            'verbosity': None,
            'unified_job_template': job_template_id,
            'all_parents_must_converge': True,
            'identifier': step_name
        }
        workflow_job_template_node_ids = self.post(body,
                                                   'workflow_job_templates/' + str(workflow_id) + '/workflow_nodes')
        return workflow_job_template_node_ids

    def create_workflow_nodes(self, parent_id, child, label, steps, topology_template_workflow_steps, workflow_id=None,
                              workflow_name=None, current_time=None):
        if not parent_id:
            raise Exception('Cannot create workflow nodes: ' + str(steps.keys()) + ' parent_id is None')
        path = 'workflow_job_template_nodes/' + str(parent_id) + '/'
        if label == 'on_success':
            path += 'success_nodes'
        if label == 'on_failure':
            path += 'failure_nodes'
        if not child in steps:
            raise Exception(
                child + ' is set as step in the workflow but could not be found in the workflow steps: ' + str(steps))
        activities = steps[child]['activities']
        step = steps[child]
        for activity in activities:
            if 'call_operation' in activity:
                call_operation = activity['call_operation']
                template_name = workflow_name + '.' + child + '_' + str(current_time)
                if 'job_template' not in topology_template_workflow_steps[template_name]:
                    raise Exception(str(topology_template_workflow_steps[
                                            template_name]) + ' with call_operation: ' + call_operation + ' has no job_template definition. Check the interface implementation')
                child_id = self.add_child_node(identifier=child,
                                               unified_job_template=topology_template_workflow_steps[template_name][
                                                   'job_template'],
                                               path=path, workflow_id=workflow_id)
                if not child_id:
                    raise Exception('Failed to create child node for: ' + child)
                for outcome in ['on_failure', 'on_success']:
                    if outcome in activity:
                        node_children = activity[outcome]
                        children = []
                        if isinstance(node_children, str):
                            children.append(node_children)
                        elif isinstance(node_children, list):
                            children = node_children
                        for child in children:
                            self.create_workflow_nodes(parent_id=child_id,
                                                       child=child,
                                                       label=outcome,
                                                       steps=steps,
                                                       topology_template_workflow_steps=topology_template_workflow_steps,
                                                       workflow_id=workflow_id,
                                                       workflow_name=workflow_name,
                                                       current_time=current_time)
        return None

    def add_child_node(self, identifier, unified_job_template, path, workflow_id=None):
        res = self.get_resources(api_path='workflow_job_template_nodes/?identifier=' + identifier, results=None)
        child_id = None
        if res:
            for child in res:
                if child['summary_fields']['workflow_job_template']['id'] == workflow_id:
                    child_id = child['id']
                    break
        body = {
            'id': child_id,
            'extra_data': {},
            'inventory': None,
            'scm_branch': '',
            'job_type': None,
            'job_tags': '',
            'skip_tags': '',
            'limit': '',
            'diff_mode': None,
            'verbosity': None,
            'unified_job_template': unified_job_template,
            'all_parents_must_converge': True,
            'identifier': identifier
        }

        try:
            res = self.post(body, path)
        except (Exception) as ex:
            raise ex
        if not child_id and res:
            child_id = res[0]
        return child_id

    def launch(self, wf_id):
        path = 'workflow_job_templates/' + str(wf_id) + '/launch'
        body = {
            'ask_limit_on_launch': False,
            'ask_scm_branch_on_launch': False
        }
        wf_job_ids = self.post(body, path)
        return wf_job_ids

    def get_job_artifacts(self, attributes_job_id):
        if not attributes_job_id:
            raise Exception('attributes_job_id is None!')
        job_output = self.get_resources(api_path='jobs/' + str(attributes_job_id) + '/')
        if not job_output:
            raise Exception('Job ID: ' + attributes_job_id + ' not found')
        if 'artifacts' not in job_output:
            raise Exception('Job ID: ' + attributes_job_id + ' has no artifacts')
        return job_output['artifacts']

    def get_attributes_job_ids(self, wf_job_id):
        workflow_nodes = self.get_workflow_nodes(wf_job_id)
        attribute_job_ids = []
        for wf_node in workflow_nodes:
            if not wf_node['success_nodes'] and not wf_node['failure_nodes'] and not wf_node['always_nodes'] and 'job' \
                    in wf_node and 'attributes' in wf_node['identifier']:
                attribute_job_ids.append(wf_node['job'])
        return attribute_job_ids

    def get_workflow_status(self, launched_id):
        workflow = self.get_resources(api_path='workflow_jobs/' + str(launched_id) + '/')
        return workflow['status']

    def get_job_status(self, job_id):
        job_template = self.get_resources(api_path='job_templates/' + str(job_id) + '/')
        return job_template['status']

    def set_tosca_node_attributes(self, tosca_template_dict, attributes):
        node_templates = tosca_template_dict['topology_template']['node_templates']
        for node_name in attributes:
            if node_name in node_templates and 'attributes' in node_templates[node_name]:
                node_attributes = node_templates[node_name]['attributes']
            else:
                node_attributes = {}
            node_attributes.update(attributes[node_name])
            if 'attributes' in node_attributes:
                node_templates[node_name]['attributes'] = node_attributes['attributes']
            elif node_name in node_templates:
                node_templates[node_name]['attributes'] = node_attributes
        return tosca_template_dict

    def add_credentials(self, credential=None, organization_id=None, path=None, name=None):
        if credential and 'get_attribute' not in credential:
            body = {}
            if 'protocol' in credential and 'ssh' == credential['protocol']:
                decoded_key = base64.b64decode(credential['keys']['private_key'])
                decoded_key_str = str(decoded_key, 'utf-8')

                body = {
                    'description': 'delete_after_execution',
                    'name': name,
                    'organization': organization_id,
                    'credential_type': 1,
                    'inputs': {
                        'ssh_key_data': decoded_key_str,
                    }
                }
            if 'cloud_provider_name' in credential:
                if credential['cloud_provider_name'] == 'Azure':
                    body = {
                        'name': name,
                        'description': 'delete_after_execution',
                        'organization': organization_id,
                        'kind': 'azure_rm',
                        'tenant': credential['extra_properties']['tenant'],
                        'client': credential['user'],
                        'secret': credential['token'],
                        'subscription': credential['extra_properties']['subscription_id'],
                        'credential_type': 10,
                        'inputs': {
                            'client': credential['user'],
                            'secret': credential['token'],
                            'tenant': credential['extra_properties']['tenant'],
                            'subscription': credential['extra_properties']['subscription_id']
                        }
                    }
                if credential['cloud_provider_name'] == 'EC2':
                    body = {
                        'name': name,
                        'description': 'delete_after_execution',
                        'organization': organization_id,
                        'kind': 'aws',
                        'credential_type': 5,
                        'inputs': {
                            'password': credential['token'],
                            'username': credential['keys']['aws_access_key_id'],
                        }
                    }
                if credential['cloud_provider_name'] == 'OpenStack':
                    body = {
                        'name': name,
                        'description': 'delete_after_execution',
                        'organization': organization_id,
                        'kind': 'openstack',
                        'credential_type': 6,
                        'inputs': {
                            'host': credential['auth_url'],
                            'domain': credential['extra_properties']['domain'],
                            'region': credential['extra_properties']['domain'],
                            'project': credential['extra_properties']['project_id'],
                            'password': credential['token'],
                            'username': credential['user'],
                            'verify_ssl': True,
                            'project_domain_name': credential['extra_properties']['domain']
                        }
                    }
            credentials = self.get_resources(
                api_path='credentials/?name=' + name + '&organization=' + str(organization_id))
            if credentials and credentials[0]:
                r = self._session.delete(self.api_url + '/credentials/' + str(credentials[0]['id']),
                                         headers=self.headers, verify=False)
            credential_ids = self.post(body, path)
            return credential_ids
        return None

    def create_organization(self, name):
        body = {
            'name': name,
            'description': '',
            'max_hosts': 99
        }
        organization_id = self.post(body, 'organizations')
        return organization_id[0]

    def delete(self, api_path, query):
        resources = self.get_resources(api_path=api_path + '/' + query)
        if resources:
            for inventory in resources:
                r = self._session.delete(self.api_url + '/' + api_path + '/' + str(inventory['id']),
                                         headers=self.headers, verify=False)
        # else:
        #     raise Exception('Response Code:'+ str(r.status_code) +' '+r.text + '\nRequest Body: ' + str(body))

    def get_variables(self, extra_variables):
        if not isinstance(extra_variables, dict):
            valid = validators.url(extra_variables)
            if valid == True:
                url = requests.get(extra_variables)
                text = url.text
                try:
                    tup_json = json.loads(text)
                    return tup_json
                except:
                    return yaml.load(text)
        elif isinstance(extra_variables, dict):
            return extra_variables

    def put(self, body, api_path):
        r = self._session.put(self.api_url + '/' + api_path + '/', data=json.dumps(body), headers=self.headers,
                              verify=False)
        ids = []
        if r.status_code == 200:
            if r.text:
                json_resp = r.json()
                if 'id' in json_resp:
                    id = json_resp['id']
                    ids.append(id)
            return ids
        else:
            raise Exception('Response Code:' + str(r.status_code) + ' ' + r.text + '\nRequest Body: ' + str(body))
        pass

    def clean_up_execution(self):
        workflows_to_delete = self.get_resources(api_path='workflow_job_templates/?description=delete_after_execution')
        for workflow in workflows_to_delete:
            r = self._session.delete(self.api_url + '/workflow_job_templates/' + str(workflow['id']), verify=False,
                                     headers=self.headers)
        inventories_to_delete = self.get_resources(api_path='inventories/?description=delete_after_execution')
        for inventory in inventories_to_delete:
            r = self._session.delete(self.api_url + '/inventories/' + str(inventory['id']), verify=False,
                                     headers=self.headers)
        job_to_delete = self.get_resources(api_path='job_templates/?description=delete_after_execution')
        for job in job_to_delete:
            r = self._session.delete(self.api_url + '/job_templates/' + str(job['id']), verify=False,
                                     headers=self.headers)
        credentials_to_delete = self.get_resources(api_path='credentials/?description=delete_after_execution')
        for credential in credentials_to_delete:
            r = self._session.delete(self.api_url + '/credentials/' + str(credential['id']), verify=False,
                                     headers=self.headers)

    def get_workflow_nodes(self, wf_job_id):
        workflow_nodes = self.get_resources(api_path='workflow_jobs/' + str(wf_job_id) + '/workflow_nodes/')
        return workflow_nodes
