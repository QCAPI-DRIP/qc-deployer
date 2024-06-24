import logging
import os
import os.path
import os.path
import re  # noqa: F401
import tempfile
import unittest
import urllib

import yaml

from service.tosca_helper import ToscaHelper

sure_tosca_base_url = 'http://localhost:8081/tosca-sure/1.0.0'
awx_base_url = 'http://localhost:8052/api/v2'
awx_username = 'admin'
awx_password = 'password'
logger = logging.getLogger(__name__)


class TestTOSCAHelper(unittest.TestCase):

    def test_resolve_function_values(self):
        tosca_template_dict = self.get_tosca_from_url(
            'https://raw.githubusercontent.com/qcdis-sdia/sdia-tosca/master/examples/articonf/TIC_ec2_wf_with_bank.yaml')

        tmp_path = tempfile.mkdtemp()
        tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
        with open(tosca_template_path, 'w') as outfile:
            yaml.dump(tosca_template_dict, outfile, default_flow_style=False)

        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        if tosca_service_is_up:
            node_templates = tosca_template_dict['topology_template']['node_templates']
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            for tosca_node_name in node_templates:
                tosca_node = node_templates[tosca_node_name]
                tosca_node = tosca_helper.resolve_function_values(tosca_node)
                interfaces = tosca_node['interfaces']
                for interface_name in interfaces:
                    interface_ancestors = tosca_helper.get_interface_ancestors(interface_name)
                    self.assertIsNotNone(interface_ancestors)

    def test(self):
        tosca_template_dict = self.get_tosca_from_url(
            'https://raw.githubusercontent.com/qcdis-sdia/sdia-tosca/master/examples/articonf/TIC_ec2_wf_with_bank.yaml')

        tmp_path = tempfile.mkdtemp()
        tosca_template_path = tmp_path + os.path.sep + 'toscaTemplate.yml'
        with open(tosca_template_path, 'w') as outfile:
            yaml.dump(tosca_template_dict, outfile, default_flow_style=False)

        tosca_service_is_up = ToscaHelper.service_is_up(sure_tosca_base_url)
        if tosca_service_is_up:
            node_templates = tosca_template_dict['topology_template']['node_templates']
            tosca_helper = ToscaHelper(sure_tosca_base_url, tosca_template_path)
            for tosca_node_name in node_templates:
                tosca_node = node_templates[tosca_node_name]
                tosca_node = tosca_helper.resolve_function_values(tosca_node)
                interfaces = tosca_node['interfaces']
                for interface_name in interfaces:
                    interface_ancestors = tosca_helper.get_interface_ancestors(interface_name)
                    self.assertIsNotNone(interface_ancestors)

    def get_tosca_from_url(self, url):
        with urllib.request.urlopen(url) as stream:
            parsed_json_message = yaml.load(stream)
        return parsed_json_message


if __name__ == '__main__':
    unittest.main()
