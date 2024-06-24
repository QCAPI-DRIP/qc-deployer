import base64
import configparser
import json
import logging
from time import sleep
import datetime

import yaml
from semaphore_client.semaphore_helper import SemaphoreHelper

yaml.Dumper.ignore_aliases = lambda *args: True

logger = logging.getLogger(__name__)
if not getattr(logger, 'handler_set', None):
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    h.setFormatter(formatter)
    logger.addHandler(h)
    logger.handler_set = True


class AnsibleService:

    def __init__(self, semaphore_base_url=None, semaphore_username=None, semaphore_password=None):
        self.semaphore_base_url = semaphore_base_url
        self.semaphore_username = semaphore_username
        self.semaphore_password = semaphore_password
        self.semaphore_helper = SemaphoreHelper(self.semaphore_base_url, self.semaphore_username,
                                                self.semaphore_password)
        self.repository_id = None
        self.template_id = None

    def execute(self, application, interface_type, vms, env_vars=None):
        name = application.name
        desired_state = None
        tasks_outputs = {}
        interfaces = application.node_template.interfaces
        current_state = 'Unknown'
        if 'current_state' in application.node_template.attributes:
            current_state = application.node_template.attributes['current_state']
        if 'desired_state' in application.node_template.attributes:
            desired_state = application.node_template.attributes['desired_state']

        if desired_state:
            now = datetime.datetime.now()
            project_id = self.semaphore_helper.create_project(application.name + '_' + str(now))
            inventory_dict = self.build_inventory(vms, application_name=application.name)
            inventory_contents = yaml.dump(inventory_dict, default_flow_style=False)
            private_key = self.get_private_key(vms)
            key_id = self.semaphore_helper.create_ssh_key(application.name, project_id, private_key)
            inventory_id = self.semaphore_helper.create_inventory(application.name, project_id, key_id,
                                                                  inventory_contents)
            if current_state != desired_state:
                if 'RUNNING' == desired_state:
                    interface = interfaces[interface_type]
                    create = interface['create']
                    inputs = create['inputs']
                    # git_url = inputs['repository']
                    task_ids = self.create_node(interfaces=interfaces, interface_type=interface_type,
                                                             env_vars=env_vars, project_id=project_id, name=name,
                                                             application=application, vms=vms, key_id=key_id,
                                                             inventory_id=inventory_id)
                    success = False
                    for task_id in task_ids:
                        tasks_outputs[task_id] = self.semaphore_helper.get_task_outputs(project_id, task_id)
                        if self.semaphore_helper.get_task(project_id, task_id).status == 'success':
                            success = True
                        else:
                            success = False
                    if 'configure' in interface and 'resources' in inputs and success:
                        task_ids = self.configure_node( interface=interface, project_id=project_id,name=name,env_vars=env_vars,key_id=key_id,inventory_id=inventory_id)
                        for task_id in task_ids:
                            tasks_outputs[task_id] = self.semaphore_helper.get_task_outputs(project_id, task_id)
                return tasks_outputs

    def build_inventory(self, vms, application_name=None):
        vars = {}
        vars ['ansible_ssh_common_args'] = '"-o StrictHostKeyChecking=no"'
        vars['ansible_ssh_user'] = vms[0].node_template.properties['user_name']
        vars['ansible_python_interpreter'] = '/usr/bin/python3'
        if application_name == 'gluster_fs' or application_name == 'glusterfs':
            return self.build_glusterfs_inventory(vms, vars)
        if application_name == 'tic':
            return self.build_tic_inventory(vms, vars)
        inventory = {}
        all = {}

        children = {}
        for vm in vms:
            attributes = vm.node_template.attributes
            roles = []
            roles.append(attributes['role'])
            public_ip = attributes['public_ip']
            for role in roles:
                if role not in children:
                    hosts = {}
                else:
                    hosts = children[role]

                host = {}
                host[public_ip] = vars
                if 'hosts' in hosts:
                    hosts['hosts'][public_ip] = vars
                else:
                    hosts['hosts'] = host
                children[role] = hosts
        all['children'] = children
        inventory['all'] = all
        return inventory

    def get_private_key(self, vms):
        private_key = vms[0].node_template.attributes['user_key_pair']['keys']['private_key']
        return base64.b64decode(private_key).decode('utf-8').replace(r'\n', '\n')

    def run_task(self, name, project_id, key_id, git_url, inventory_id, playbook_name, environment_id=None,
                 arguments=None):
        logger.info('project_id: ' + str(project_id) + ' task name: ' + str(
            name) + ' git url: ' + git_url + ' playbook: ' + playbook_name)
        self.repository_id = self.semaphore_helper.create_repository(name, project_id, key_id, git_url)
        template_id = self.semaphore_helper.create_template(project_id, key_id, inventory_id, self.repository_id,
                                                            playbook_name, arguments)
        task_id = self.semaphore_helper.execute_task(project_id, template_id, playbook_name,
                                                     environment_id=environment_id)
        task = self.semaphore_helper.get_task(project_id, task_id)
        last_status = ''
        while task.status == 'waiting' or task.status == 'running':
            task = self.semaphore_helper.get_task(project_id, task_id)
            this_status = task.status
            if last_status != this_status:
                logger.info('task name: ' + name + ', task status: ' + str(task.status))
            last_status = this_status
            sleep(6)
        return task_id

    def build_glusterfs_inventory(self, vms, vars):
        inventory = {}
        all = {}
        children = {}
        gfs_count = 1
        for vm in vms:
            attributes = vm.node_template.attributes
            roles = []
            roles.append('gfscluster')
            public_ip = attributes['public_ip']
            vm_vars = {'ansible_host': public_ip}
            vm_vars.update(vars)

            for role in roles:
                if role not in children:
                    hosts = {}
                else:
                    hosts = children[role]
                if 'hosts' in hosts:
                    hosts['hosts']['gfs' + str(gfs_count)] = vm_vars

                else:
                    host = {}
                    host['gfs' + str(gfs_count)] = vm_vars
                    hosts['hosts'] = host
                    gfs_count += 1
                children[role] = hosts
        all['children'] = children
        inventory['all'] = all
        return inventory

    def build_tic_inventory(self, vms, vars):
        inventory = {}
        all = {}
        children = {}
        for vm in vms:
            attributes = vm.node_template.attributes
            public_ip = attributes['public_ip']
            vm_vars = {'ansible_host': public_ip}
            vm_vars['ansible_ssh_common_args']='-o StrictHostKeyChecking=no'
            vm_vars.update(vars)
            roles = []
            if attributes['role'] == 'master':
                roles.append('swarm_manager_prime')
                roles.append('swarm_managers')
            elif attributes['role'] == 'worker':
                roles.append('swarm_workers')
            fabric_count = 0
            for role in roles:
                if role not in children:
                    hosts = {}
                else:
                    hosts = children[role]
                if 'hosts' in hosts:
                    if role == 'swarm_manager_prime' or role == 'swarm_managers':
                        host = {'hlf' + str(fabric_count): vm_vars}
                    else:
                        fabric_count += 1
                        host = {'hlf' + str(fabric_count): vm_vars}
                    hosts['hosts'] = host
                else:
                    if role == 'swarm_manager_prime' or role == 'swarm_managers':
                        host = {'hlf' + str(fabric_count): vm_vars}
                    else:
                        fabric_count += 1
                        host = {'hlf' + str(fabric_count): vm_vars}
                    hosts['hosts'] = host
                children[role] = hosts
        all['children'] = children
        inventory['all'] = all
        return inventory

    def create_node(self, interfaces=None, interface_type=None, env_vars=None, project_id=None, name=None, application=None, vms=None,
                    key_id=None, inventory_id=None):
        interface = interfaces[interface_type]
        create = interface['create']
        inputs = create['inputs']
        git_url = inputs['repository']
        task_ids = []
        if 'resources' in inputs:
            playbook_names = inputs['resources']
            for playbook_name in playbook_names:
                environment_id = None
                if env_vars:
                    environment_id = self.semaphore_helper.create_environment(project_id, name, env_vars)
                arguments = None
                if application.name == 'gluster_fs' or application.name == 'glusterfs' or application.name == 'tic':
                    for vm in vms:
                        attributes = vm.node_template.attributes
                        if attributes['role'] == 'master':
                            master_ip = attributes['public_ip']
                            break
                    arguments = '["-u","vm_user","--extra-vars","gluster_cluster_host0=\'' + master_ip \
                                + '\' gluster_cluster_volume=\'gfs0\' devmode=\'false\' ' \
                                  'device_path=\'/dev/xvdh\' gfs_size=\'15G\'"]'
                task_id = self.run_task(name, project_id, key_id, git_url, inventory_id, playbook_name,
                                        environment_id=environment_id, arguments=arguments)
                if task_id not in task_ids:
                    task_ids.append(task_id)
                count = 0
                while self.semaphore_helper.get_task(project_id, task_id).status != 'success':
                    task_id = self.run_task(name, project_id, key_id, git_url, inventory_id, playbook_name,
                                            environment_id=environment_id, arguments=arguments)
                    if task_id not in task_ids:
                        task_ids.append(task_id)
                    count += 1
                    if count >= 3:
                        msg = ' '
                        for out in self.semaphore_helper.get_task_outputs(project_id, task_id):
                            msg = msg + ' ' + out.output
                        raise Exception(
                            'Task: ' + playbook_name + ' failed. ' + self.semaphore_helper.get_task(project_id,
                                                                                                    task_id).status + ' Output: ' + msg)
        return task_ids

    def configure_node(self, interface=None, project_id=None,name=None,env_vars=None,key_id=None,inventory_id=None):
        configure = interface['configure']
        inputs = configure['inputs']
        git_url = inputs['repository']
        playbook_names = inputs['resources']
        task_ids = []
        for playbook_name in playbook_names:
            environment_id = None
            if env_vars:
                environment_id = self.semaphore_helper.create_environment(project_id, name, env_vars)
            count = 0
            task_id = self.run_task(name, project_id, key_id, git_url, inventory_id, playbook_name,
                                    environment_id=environment_id)

            while self.semaphore_helper.get_task(project_id, task_id).status != 'success':
                task_id = self.run_task(name, project_id, key_id, git_url, inventory_id, playbook_name,
                                        environment_id=environment_id)
                if task_id not in task_ids:
                    task_ids.append(task_id)
                count += 1
                if count >= 3:
                    msg = ' '
                    for out in self.semaphore_helper.get_task_outputs(project_id, task_id):
                        msg = msg + ' ' + out.output
                    raise Exception(
                        'Task: ' + playbook_name + ' failed. ' + self.semaphore_helper.get_task(project_id,
                                                                                                task_id).status + ' Output: ' + msg)
        return task_ids