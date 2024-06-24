import logging
import os
import sys
import urllib.request

import networkx as nx
import yaml
from sure_tosca_client import Configuration, ApiClient
from sure_tosca_client.api import default_api
from toscaparser.tosca_template import ToscaTemplate

logger = logging.getLogger(__name__)


def check_workflow_preconditions(workflow=None, tosca_template_dict=None):
    if 'preconditions' in workflow:
        preconditions = workflow['preconditions']
        predicate_conditions_check = False
        preconditions_check = False
        for precondition in preconditions:
            target_name = precondition['target']
            conditions = precondition['condition']
            conditions_count = 0
            for condition in conditions:
                predicate = 'assert'
                if 'assert' in condition:
                    predicate = 'assert'
                predicate_conditions = condition[predicate]
                target = tosca_template_dict['topology_template']['node_templates'][target_name]
                num_of_conditions = len(predicate_conditions)
                for predicate_condition in predicate_conditions:
                    attribute_name = list(predicate_condition.keys())[0]
                    operator = list(predicate_condition[attribute_name][0].keys())[0]
                    attribute_value = predicate_condition[attribute_name][0][operator]
                    # I'm sure there is a better way
                    if 'attributes' in target and attribute_name in target['attributes']:
                        if operator == 'equal':
                            if target['attributes'][attribute_name] == attribute_value:
                                conditions_count += 1
                            else:
                                return False
                    else:
                        return False
                if conditions_count == num_of_conditions:
                    predicate_conditions_check = True
        return predicate_conditions_check
    return False


def get_tosca_template(tosca_template_dict):
    if 'workflows' in tosca_template_dict['topology_template']:
        workflows = tosca_template_dict['topology_template'].pop('workflows')
        logger.info("Ignoring  workflows: " + str(workflows))
    tt = ToscaTemplate(yaml_dict_tpl=tosca_template_dict)
    return tt


def init_sure_tosca_client(sure_tosca_base_path):
    configuration = Configuration()
    configuration.host = sure_tosca_base_path
    api_client = ApiClient(configuration=configuration)
    api = default_api.DefaultApi(api_client=api_client)
    return api


def get_interface_types(target):
    interface_types = []
    for interface in target.node_template.interfaces:
        interface_types.append(interface)

    return interface_types


def set_node(updated_node, tosca_template_dict):
    node_templates = tosca_template_dict['topology_template']['node_templates']
    for node_name in node_templates:
        if node_name == updated_node.name:
            node_templates[node_name] = updated_node.node_template.to_dict()
            return tosca_template_dict


class ToscaHelper:

    def __init__(self, sure_tosca_base_url, tosca_template_path):
        self.function_names = {'get_property': 'properties', 'get_attribute': 'attributes', 'get_input': 'inputs'}
        self.sure_tosca_base_url = sure_tosca_base_url
        self.tosca_template_path = tosca_template_path

        self.tosca_template_dict = self.get_tosca_from_file(tosca_template_path)
        self.tosca_client = init_sure_tosca_client(sure_tosca_base_url)
        logger.info('Uploading: ' + tosca_template_path)
        self.doc_id = self.upload_tosca_template(tosca_template_path)
        self.tt = get_tosca_template(self.tosca_template_dict)
        self.all_types = {}
        tosca_node_types = self.tt.nodetemplates[0].type_definition.TOSCA_DEF
        all_custom_def = self.tt.nodetemplates[0].custom_def
        self.all_types.update(tosca_node_types)
        self.all_types.update(all_custom_def)
        logger.info('Got from sure_tosca id: ' + str(self.doc_id))

    def get_interface_ancestors(self, interface_name, derived=None):
        for interface_type_name in self.all_types:
            if 'tosca.interfaces' in interface_type_name and interface_name in interface_type_name:
                if not derived:
                    derived = []
                interface = self.all_types[interface_type_name]
                if 'derived_from' in interface:
                    derived.append(interface['derived_from'])
                    self.get_interface_ancestors(interface['derived_from'], derived)
        return derived

    def upload_tosca_template(self, file_path):
        file_id = self.tosca_client.upload_tosca_template(file_path)
        return file_id

    def get_application_nodes(self):
        return self.tosca_client.get_node_templates(self.doc_id, type_name='tosca.nodes.QC.Application')

    def get_vm_topologies(self):
        return self.tosca_client.get_node_templates(self.doc_id, type_name='tosca.nodes.QC.VM.topology')

    def get_deployment_node_pipeline(self):
        vm_topologies = self.get_vm_topologies()
        nodes_to_deploy = []
        for vm_topology_map in vm_topologies:
            vm_topology = vm_topology_map.node_template
            if hasattr(vm_topology, 'attributes') and \
                    'desired_state' in vm_topology.attributes and \
                    vm_topology.attributes['desired_state'] == 'RUNNING':
                if 'current_state' in vm_topology.attributes and \
                        vm_topology.attributes['current_state'] == 'RUNNING':
                    continue
                else:
                    nodes_to_deploy.append(vm_topology_map)
        nodes_to_deploy.extend(self.get_application_nodes())
        G = nx.DiGraph()
        sorted_nodes = []
        for node in nodes_to_deploy:
            related_nodes = self.tosca_client.get_related_nodes(self.doc_id, node.name)
            for related_node in related_nodes:
                G.add_edge(node.name, related_node.name)
            #     # We need to deploy the docker orchestrator on the VMs not the topology.
            #     # But the topology is directly connected to the orchestrator not the VMs.
            #     # So we explicitly get the VMs
            #     # I don't like this solution but I can't think of something better.
            #     if related_node.node_template.type == 'tosca.nodes.QC.VM.topology':
            #         vms = self.get_vms()
            #         related_node = vms
            #     pair = (related_node, node)
            #     nodes_pairs.append(pair)
        sorted_graph = sorted(G.in_degree, key=lambda x: x[1], reverse=True)
        for node_tuple in sorted_graph:
            node_name = node_tuple[0]
            for node in nodes_to_deploy:
                if node.name == node_name:
                    sorted_nodes.append(node)
        return sorted_nodes

    @classmethod
    def service_is_up(cls, url):
        code = None
        try:
            code = urllib.request.urlopen(url).getcode()
        except Exception as e:
            if hasattr(e, 'code') and e.code == 404:
                return True
            exc_type, exc_obj, exc_tb = sys.exc_info()
            os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            return False
            # if not e.reason and not e.reason.errno and e.code:
            #     return False
            # else:
            #     return True

        return True

    def get_vms(self):
        return self.tosca_client.get_node_templates(self.doc_id, type_name='tosca.nodes.QC.VM.Compute')

    def get_workflows(self):
        with open(self.tosca_template_path) as file:
            # The FullLoader parameter handles the conversion from YAML
            # scalar values to Python the dictionary format
            tosca_template = yaml.load(file, Loader=yaml.FullLoader)

        if 'workflows' in tosca_template['topology_template']:
            return tosca_template['topology_template']['workflows']

    def get_function_value(self, function):
        target_node = self.tosca_template_dict['topology_template']['node_templates'][function['target']]
        name = self.function_names[function['name']]
        value_name = function['value_name']
        if name in target_node and value_name in target_node[name]:
            return target_node[name][value_name]

    @classmethod
    def get_tosca_from_file(cls, path):
        with open(path) as json_file:
            return yaml.safe_load(json_file)

    def resolve_function_values(self, tosca_node):
        functions = []
        functions = self.find_functions(tosca_node, functions=functions)
        for function in functions:
            value = self.get_function_value(function)
            if value:
                tosca_node = self.replace_value(tosca_node, function, value)
        return tosca_node

    def replace_value(self, obj, function, replace_value):
        for k, v in obj.items():
            if isinstance(v, list):
                i = 0
                for elem in v:
                    if isinstance(elem, dict):
                        v[i] = self.replace_value(elem, function, replace_value)
                    i += 1
            if isinstance(v, dict):
                obj[k] = self.replace_value(v, function, replace_value)
        if function['name'] in obj and \
                obj[function['name']][0] == function['target'] and \
                obj[function['name']][1] == function['value_name']:
            obj = replace_value
        return obj

    def find_functions(self, d, functions=None):
        for k, v in d.items():
            for function_name in self.function_names:
                if function_name == k:
                    function = {'name': function_name, 'target': d[function_name][0], 'value_name': d[function_name][1]}
                    functions.append(function)
            if isinstance(v, list):
                for elem in v:
                    if isinstance(elem, dict):
                        self.find_functions(elem, functions=functions)
            if isinstance(v, dict):
                self.find_functions(v, functions=functions)
        return functions

    def set_node_state(self, tosca_template_dict=None, job=None, workflow_name=None, current_time=None):
        workflow_name_no_time = workflow_name.split('_' + str(current_time))[0]
        wf_steps = self.get_workflows()[workflow_name_no_time]['steps']

        state = None
        target_wf_step_name = job['name'].split(workflow_name + '.')[1].split('_' + str(current_time))[0]
        # Match job name with call_operation
        wf_step = wf_steps[target_wf_step_name]
        if 'target' not in wf_step:
            raise Exception('workflow step: ' + str(wf_step) + ' has no target')
        target_name = wf_step['target']

        if target_name and target_wf_step_name:
            # Should we update the state?
            wf_step = wf_steps[target_wf_step_name]
            activities = wf_step['activities']
            index = 0
            for activity in activities:
                index += 1
                if job['status'] == 'failed':
                    state = 'FAILED'
                    break
                if job['status'] == 'running' and 'set_state' in activity and index <= 1:
                    state = activity['set_state']
                    break
                if job['status'] == 'successful' and 'set_state' in activity and index > 1:
                    state = activity['set_state']
                    break
        if target_name and state:
            logger.info('wf step: ' + str(wf_step) + ' node: ' + target_name + ' state: ' + state)
            target = tosca_template_dict['topology_template']['node_templates'][target_name]
            if 'attributes' in target:
                attributes = target['attributes']
            else:
                attributes = {}
            attributes['current_state'] = state
            target['attributes'] = attributes
        return tosca_template_dict

    @staticmethod
    def extract_credentials_from_node(tosca_node):
        credentials = []
        for name in ['attributes', 'properties']:
            if name in tosca_node:
                for cred_name in ['credential', 'credentials', 'user_key_pair']:
                    if cred_name in tosca_node[name]:
                        credential = tosca_node[name][cred_name]
                        if isinstance(credential, list):
                            credentials.extend(credential)
                        else:
                            credentials.append(credential)
        return credentials


def get_interface_types(node):
    interface_type_names = []
    if node.node_template.interfaces:
        for interface in node.node_template.interfaces:
            interface_type_names.append(interface)
        return interface_type_names
