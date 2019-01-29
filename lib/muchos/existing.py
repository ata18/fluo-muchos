#!/usr/bin/env python3
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import shutil
import subprocess
import time
from os.path import isfile, join
from sys import exit

from .config import HOST_VAR_DEFAULTS, PLAY_VAR_DEFAULTS


class ExistingCluster:

    def __init__(self, config):
        self.config = config

    def launch(self):
        exit('ERROR - Attempting to launch when cluster_type is set to "existing"')

    def sync(self):
        config = self.config
        print('Syncing ansible directory on {0} cluster proxy node'.format(config.cluster_name))

        host_vars = HOST_VAR_DEFAULTS
        play_vars = PLAY_VAR_DEFAULTS

        for section in ("general", "ansible-vars", config.get('performance', 'profile')):
            for (name, value) in config.items(section):
                if name not in ('proxy_hostname', 'proxy_socks_port'):
                    if name in host_vars:
                        host_vars[name] = value
                    if name in play_vars:
                        play_vars[name] = value

        play_vars['accumulo_sha256'] = config.checksum('accumulo')
        play_vars['fluo_sha256'] = config.checksum('fluo')
        play_vars['fluo_yarn_sha256'] = config.checksum('fluo_yarn')
        play_vars['hadoop_sha256'] = config.checksum('hadoop')
        play_vars['spark_sha256'] = config.checksum('spark')
        play_vars['zookeeper_sha256'] = config.checksum('zookeeper')

        cluster_type = host_vars.get('cluster_type', 'ec2')
        node_type_map = {}
        if cluster_type == 'ec2':
            node_type_map = config.node_type_map()
            play_vars["mount_root"] = config.mount_root
            play_vars["metrics_drive_ids"] = config.metrics_drive_ids()
            play_vars["fstype"] = config.fstype()
            play_vars["force_format"] = config.force_format()
            play_vars["shutdown_delay_minutes"] = config.get("ec2", "shutdown_delay_minutes")
        if cluster_type == 'existing':
            play_vars["mount_root"] = config.get("existing", "mount_root")
            play_vars["metrics_drive_ids"] = config.get("existing", "metrics_drives_ids").split(",")
            mounts = config.get("existing", "mounts").split(",")
            devices = config.get("existing", "devices").split(",")
            for node_type in 'default', 'worker':
                node_type_map[node_type] = {'mounts': mounts, 'devices': devices}

        play_vars["node_type_map"] = node_type_map
        host_vars['worker_data_dirs'] = str(node_type_map['worker']['mounts'])
        host_vars['default_data_dirs'] = str(node_type_map['default']['mounts'])

        with open(join(config.deploy_path, "ansible/site.yml"), 'w') as site_file:
            print("- import_playbook: common.yml", file=site_file)
            if config.has_service("spark"):
                print("- import_playbook: spark.yml", file=site_file)
            print("- import_playbook: hadoop.yml", file=site_file)
            print("- import_playbook: zookeeper.yml", file=site_file)
            if config.has_service("metrics"):
                print("- import_playbook: metrics.yml", file=site_file)
            print("- import_playbook: accumulo.yml", file=site_file)
            if config.has_service('fluo'):
                print("- import_playbook: fluo.yml", file=site_file)
            if config.has_service('fluo_yarn'):
                print("- import_playbook: fluo_yarn.yml", file=site_file)
            if config.has_service("mesosmaster"):
                print("- import_playbook: mesos.yml", file=site_file)
            if config.has_service("swarmmanager"):
                print("- import_playbook: docker.yml", file=site_file)

        ansible_conf = join(config.deploy_path, "ansible/conf")
        with open(join(ansible_conf, "hosts"), 'w') as hosts_file:
            print("[proxy]\n{0}".format(config.proxy_hostname()), file=hosts_file)
            print("\n[accumulomaster]\n{0}".format(config.get_service_hostnames("accumulomaster")[0]), file=hosts_file)
            print("\n[namenode]\n{0}".format(config.get_service_hostnames("namenode")[0]), file=hosts_file)
            print("\n[resourcemanager]\n{0}".format(config.get_service_hostnames("resourcemanager")[0]),
                  file=hosts_file)
            if config.has_service("spark"):
                print("\n[spark]\n{0}".format(config.get_service_hostnames("spark")[0]), file=hosts_file)
            if config.has_service("mesosmaster"):
                print("\n[mesosmaster]\n{0}".format(config.get_service_hostnames("mesosmaster")[0]), file=hosts_file)
            if config.has_service("metrics"):
                print("\n[metrics]\n{0}".format(config.get_service_hostnames("metrics")[0]), file=hosts_file)
            if config.has_service("swarmmanager"):
                print("\n[swarmmanager]\n{0}".format(config.get_service_hostnames("swarmmanager")[0]), file=hosts_file)

            print("\n[zookeepers]", file=hosts_file)
            for (index, zk_host) in enumerate(config.get_service_hostnames("zookeeper"), start=1):
                print("{0} id={1}".format(zk_host, index), file=hosts_file)

            if config.has_service('fluo'):
                print("\n[fluo]", file=hosts_file)
                for host in config.get_service_hostnames("fluo"):
                    print(host, file=hosts_file)

            if config.has_service('fluo_yarn'):
                print("\n[fluo_yarn]", file=hosts_file)
                for host in config.get_service_hostnames("fluo_yarn"):
                    print(host, file=hosts_file)

            print("\n[workers]", file=hosts_file)
            for worker_host in config.get_service_hostnames("worker"):
                print(worker_host, file=hosts_file)

            print("\n[accumulo:children]\naccumulomaster\nworkers", file=hosts_file)
            print("\n[hadoop:children]\nnamenode\nresourcemanager\nworkers", file=hosts_file)

            print("\n[nodes]", file=hosts_file)
            for (private_ip, hostname) in config.get_private_ip_hostnames():
                print("{0} ansible_ssh_host={1} node_type={2}".format(hostname, private_ip,
                                                                      config.node_type(hostname)), file=hosts_file)

            print("\n[all:vars]", file=hosts_file)
            for (name, value) in sorted(host_vars.items()):
                print("{0} = {1}".format(name, value), file=hosts_file)

        with open(join(config.deploy_path, "ansible/group_vars/all"), 'w') as play_vars_file:
            for (name, value) in sorted(play_vars.items()):
                print("{0}: {1}".format(name, value), file=play_vars_file)

        # copy keys file to ansible/conf (if it exists)
        conf_keys = join(config.deploy_path, "conf/keys")
        ansible_keys = join(ansible_conf, "keys")
        if isfile(conf_keys):
            shutil.copyfile(conf_keys, ansible_keys)
        else:
            open(ansible_keys, 'w').close()

        basedir = config.get('general', 'cluster_basedir')
        cmd = "rsync -az --delete -e \"ssh -o 'StrictHostKeyChecking no'\""
        subprocess.call("{cmd} {src} {usr}@{ldr}:{tdir}".format(cmd=cmd, src=join(config.deploy_path, "ansible"),
                                                                usr=config.get('general', 'cluster_user'),
                                                                ldr=config.get_proxy_ip(), tdir=basedir),
                        shell=True)

        self.exec_on_proxy_verified("{0}/ansible/scripts/install_ansible.sh".format(basedir), opts='-t')

    def setup(self):
        config = self.config
        print('Setting up {0} cluster'.format(config.cluster_name))

        self.sync()

        conf_upload = join(config.deploy_path, "conf/upload")
        accumulo_tarball = join(conf_upload, "accumulo-{0}-bin.tar.gz".format(config.version("accumulo")))
        fluo_tarball = join(conf_upload, "fluo-{0}-bin.tar.gz".format(config.version("fluo")))
        fluo_yarn_tarball = join(conf_upload, "fluo-yarn-{0}-bin.tar.gz".format(config.version("fluo_yarn")))
        basedir = config.get('general', 'cluster_basedir')
        cluster_tarballs = "{0}/tarballs".format(basedir)
        self.exec_on_proxy_verified("mkdir -p {0}".format(cluster_tarballs))
        if isfile(accumulo_tarball):
            self.send_to_proxy(accumulo_tarball, cluster_tarballs)
        if isfile(fluo_tarball) and config.has_service('fluo'):
            self.send_to_proxy(fluo_tarball, cluster_tarballs)
        if isfile(fluo_yarn_tarball) and config.has_service('fluo_yarn'):
            self.send_to_proxy(fluo_yarn_tarball, cluster_tarballs)

        self.execute_playbook("site.yml")

    @staticmethod
    def status():
        exit("ERROR - 'status' command cannot be used when cluster_type=existing")

    @staticmethod
    def terminate():
        exit("ERROR - 'terminate' command cannot be used when cluster_type=existing")

    def ssh(self):
        self.wait_until_proxy_ready()
        fwd = ''
        if self.config.has_option('general', 'proxy_socks_port'):
            fwd = "-D " + self.config.get('general', 'proxy_socks_port')
        ssh_command = "ssh -C -A -o 'StrictHostKeyChecking no' {fwd} {usr}@{ldr}".format(
            usr=self.config.get('general', 'cluster_user'), ldr=self.config.get_proxy_ip(), fwd=fwd)
        print("Logging into proxy using: {0}".format(ssh_command))
        retcode = subprocess.call(ssh_command, shell=True)
        if retcode != 0:
            exit("ERROR - Command failed with return code of {0}: {1}".format(retcode, ssh_command))

    def exec_on_proxy(self, command, opts=''):
        ssh_command = "ssh -A -o 'StrictHostKeyChecking no' {opts} {usr}@{ldr} '{cmd}'".format(
            usr=self.config.get('general', 'cluster_user'),
            ldr=self.config.get_proxy_ip(), cmd=command, opts=opts)
        return subprocess.call(ssh_command, shell=True), ssh_command

    def exec_on_proxy_verified(self, command, opts=''):
        (retcode, ssh_command) = self.exec_on_proxy(command, opts)
        if retcode != 0:
            exit("ERROR - Command failed with return code of {0}: {1}".format(retcode, ssh_command))

    def wait_until_proxy_ready(self):
        cluster_user = self.config.get('general', 'cluster_user')
        print("Checking if '{0}' proxy can be reached using: ssh {1}@{2}"
              .format(self.config.proxy_hostname(), cluster_user, self.config.get_proxy_ip()))
        while True:
            (retcode, ssh_command) = self.exec_on_proxy('pwd > /dev/null')
            if retcode == 0:
                print("Connected to proxy using SSH!")
                time.sleep(1)
                break
            print("Proxy could not be accessed using SSH.  Will retry in 5 sec...")
            time.sleep(5)

    def execute_playbook(self, playbook):
        print("Executing '{0}' playbook".format(playbook))
        basedir = self.config.get('general', 'cluster_basedir')
        self.exec_on_proxy_verified("time -p ansible-playbook {base}/ansible/{playbook}"
                                    .format(base=basedir, playbook=playbook), opts='-t')

    def send_to_proxy(self, path, target, skip_if_exists=True):
        print("Copying to proxy: ", path)
        cmd = "scp -o 'StrictHostKeyChecking no'"
        if skip_if_exists:
            cmd = "rsync --update --progress -e \"ssh -o 'StrictHostKeyChecking no'\""
        subprocess.call("{cmd} {src} {usr}@{ldr}:{tdir}".format(
            cmd=cmd, src=path, usr=self.config.get('general', 'cluster_user'), ldr=self.config.get_proxy_ip(),
            tdir=target), shell=True)

    def perform(self, action):
        if action == 'launch':
            self.launch()
        elif action == 'status':
            self.status()
        elif action == 'sync':
            self.sync()
        elif action == 'setup':
            self.setup()
        elif action == 'ssh':
            self.ssh()
        elif action in ('wipe', 'kill', 'cancel_shutdown'):
            if not isfile(self.config.hosts_path):
                exit("Hosts file does not exist for cluster: " + self.config.hosts_path)
            if action == 'wipe':
                print("Killing all processes started by Muchos and wiping Muchos data from {0} cluster"
                      .format(self.config.cluster_name))
            elif action == 'kill':
                print("Killing all processes started by Muchos on {0} cluster".format(self.config.cluster_name))
            elif action == 'cancel_shutdown':
                print("Cancelling automatic shutdown of {0} cluster".format(self.config.cluster_name))
            self.execute_playbook(action + ".yml")
        elif action == 'terminate':
            self.terminate()
        else:
            print('ERROR - Unknown action:', action)