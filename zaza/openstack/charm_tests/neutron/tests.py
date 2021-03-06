#!/usr/bin/env python3

# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Encapsulating `neutron-openvswitch` testing."""


import logging
import tenacity
import unittest

import zaza
import zaza.openstack.charm_tests.glance.setup as glance_setup
import zaza.openstack.charm_tests.nova.utils as nova_utils
import zaza.openstack.charm_tests.test_utils as test_utils
import zaza.openstack.configure.guest as guest
import zaza.openstack.utilities.openstack as openstack_utils


class NeutronGatewayTest(test_utils.OpenStackBaseTest):
    """Test basic Neutron Gateway Charm functionality."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running Neutron Gateway tests."""
        super(NeutronGatewayTest, cls).setUpClass()
        cls.current_os_release = openstack_utils.get_os_release()
        cls.services = cls._get_services()

        bionic_stein = openstack_utils.get_os_release('bionic_stein')

        cls.pgrep_full = (True if cls.current_os_release >= bionic_stein
                          else False)

    def test_900_restart_on_config_change(self):
        """Checking restart happens on config change.

        Change disk format and assert then change propagates to the correct
        file and that services are restarted as a result
        """
        # Expected default and alternate values
        current_value = zaza.model.get_application_config(
            'neutron-gateway')['debug']['value']
        new_value = str(not bool(current_value)).title()
        current_value = str(current_value).title()

        set_default = {'debug': current_value}
        set_alternate = {'debug': new_value}
        default_entry = {'DEFAULT': {'debug': [current_value]}}
        alternate_entry = {'DEFAULT': {'debug': [new_value]}}

        # Config file affected by juju set config change
        conf_file = '/etc/neutron/neutron.conf'

        # Make config change, check for service restarts
        logging.info(
            'Setting verbose on neutron-api {}'.format(set_alternate))
        self.restart_on_changed(
            conf_file,
            set_default,
            set_alternate,
            default_entry,
            alternate_entry,
            self.services,
            pgrep_full=self.pgrep_full)

    def test_910_pause_and_resume(self):
        """Run pause and resume tests.

        Pause service and check services are stopped then resume and check
        they are started
        """
        with self.pause_resume(
                self.services,
                pgrep_full=self.pgrep_full):
            logging.info("Testing pause resume")

    def test_920_change_aa_profile(self):
        """Test changing the Apparmor profile mode."""
        services = ['neutron-openvswitch-agent',
                    'neutron-dhcp-agent',
                    'neutron-l3-agent',
                    'neutron-metadata-agent',
                    'neutron-metering-agent']

        set_default = {'aa-profile-mode': 'disable'}
        set_alternate = {'aa-profile-mode': 'complain'}

        mtime = zaza.model.get_unit_time(
            self.lead_unit,
            model_name=self.model_name)
        logging.debug('Remote unit timestamp {}'.format(mtime))

        with self.config_change(set_default, set_alternate):
            for unit in zaza.model.get_units('neutron-gateway',
                                             model_name=self.model_name):
                logging.info('Checking number of profiles in complain '
                             'mode in {}'.format(unit.entity_id))
                run = zaza.model.run_on_unit(
                    unit.entity_id,
                    'aa-status --complaining',
                    model_name=self.model_name)
                output = run['Stdout']
                self.assertTrue(int(output) >= len(services))

    @classmethod
    def _get_services(cls):
        """
        Return the services expected in Neutron Gateway.

        :returns: A list of services
        :rtype: list[str]
        """
        services = ['neutron-dhcp-agent',
                    'neutron-metadata-agent',
                    'neutron-metering-agent',
                    'neutron-openvswitch-agent']

        trusty_icehouse = openstack_utils.get_os_release('trusty_icehouse')
        xenial_newton = openstack_utils.get_os_release('xenial_newton')
        bionic_train = openstack_utils.get_os_release('bionic_train')

        if cls.current_os_release <= trusty_icehouse:
            services.append('neutron-vpn-agent')
        if cls.current_os_release < xenial_newton:
            services.append('neutron-lbaas-agent')
        if xenial_newton <= cls.current_os_release < bionic_train:
            services.append('neutron-lbaasv2-agent')

        return services


class NeutronApiTest(test_utils.OpenStackBaseTest):
    """Test basic Neutron API Charm functionality."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running Neutron Gateway tests."""
        super(NeutronApiTest, cls).setUpClass()
        cls.current_os_release = openstack_utils.get_os_release()

        # set up clients
        cls.neutron_client = (
            openstack_utils.get_neutron_session_client(cls.keystone_session))

    def test_400_create_network(self):
        """Create a network, verify that it exists, and then delete it."""
        logging.debug('Creating neutron network...')
        self.neutron_client.format = 'json'
        net_name = 'test_net'

        # Verify that the network doesn't exist
        networks = self.neutron_client.list_networks(name=net_name)
        net_count = len(networks['networks'])
        assert net_count == 0, (
            "Expected zero networks, found {}".format(net_count))

        # Create a network and verify that it exists
        network = {'name': net_name}
        self.neutron_client.create_network({'network': network})

        networks = self.neutron_client.list_networks(name=net_name)
        logging.debug('Networks: {}'.format(networks))
        net_len = len(networks['networks'])
        assert net_len == 1, (
            "Expected 1 network, found {}".format(net_len))

        logging.debug('Confirming new neutron network...')
        network = networks['networks'][0]
        assert network['name'] == net_name, "network ext_net not found"

        # Cleanup
        logging.debug('Deleting neutron network...')
        self.neutron_client.delete_network(network['id'])

    def test_401_enable_qos(self):
        """Check qos settings set via neutron-api charm."""
        if (self.current_os_release >=
                openstack_utils.get_os_release('trusty_mitaka')):
            logging.info('running qos check')

            with self.config_change(
                    {'enable-qos': 'False'},
                    {'enable-qos': 'True'},
                    application_name="neutron-api"):

                self._validate_openvswitch_agent_qos()

    def test_900_restart_on_config_change(self):
        """Checking restart happens on config change.

        Change disk format and assert then change propagates to the correct
        file and that services are restarted as a result
        """
        # Expected default and alternate values
        current_value = zaza.model.get_application_config(
            'neutron-api')['debug']['value']
        new_value = str(not bool(current_value)).title()
        current_value = str(current_value).title()

        set_default = {'debug': current_value}
        set_alternate = {'debug': new_value}
        default_entry = {'DEFAULT': {'debug': [current_value]}}
        alternate_entry = {'DEFAULT': {'debug': [new_value]}}

        # Config file affected by juju set config change
        conf_file = '/etc/neutron/neutron.conf'

        # Make config change, check for service restarts
        logging.info(
            'Setting verbose on neutron-api {}'.format(set_alternate))
        bionic_stein = openstack_utils.get_os_release('bionic_stein')
        if openstack_utils.get_os_release() >= bionic_stein:
            pgrep_full = True
        else:
            pgrep_full = False
        self.restart_on_changed(
            conf_file,
            set_default,
            set_alternate,
            default_entry,
            alternate_entry,
            ['neutron-server'],
            pgrep_full=pgrep_full)

    def test_901_pause_resume(self):
        """Run pause and resume tests.

        Pause service and check services are stopped then resume and check
        they are started
        """
        bionic_stein = openstack_utils.get_os_release('bionic_stein')
        if openstack_utils.get_os_release() >= bionic_stein:
            pgrep_full = True
        else:
            pgrep_full = False
        with self.pause_resume(
                ["neutron-server", "apache2", "haproxy"],
                pgrep_full=pgrep_full):
            logging.info("Testing pause resume")

    @tenacity.retry(wait=tenacity.wait_exponential(min=5, max=60))
    def _validate_openvswitch_agent_qos(self):
        """Validate that the qos extension is enabled in the ovs agent."""
        # obtain the dhcp agent to identify the neutron-gateway host
        dhcp_agent = self.neutron_client.list_agents(
            binary='neutron-dhcp-agent')['agents'][0]
        neutron_gw_host = dhcp_agent['host']
        logging.debug('neutron gw host: {}'.format(neutron_gw_host))

        # check extensions on the ovs agent to validate qos
        ovs_agent = self.neutron_client.list_agents(
            binary='neutron-openvswitch-agent',
            host=neutron_gw_host)['agents'][0]

        self.assertIn('qos', ovs_agent['configurations']['extensions'])


class SecurityTest(test_utils.OpenStackBaseTest):
    """Neutron Security Tests."""

    def test_security_checklist(self):
        """Verify expected state with security-checklist."""
        expected_failures = []
        expected_passes = [
            'validate-file-ownership',
            'validate-file-permissions',
        ]
        expected_to_pass = True

        # override settings depending on application name so we can reuse
        # the class for multiple charms
        if self.application_name == 'neutron-api':
            tls_checks = [
                'validate-uses-tls-for-keystone',
            ]

            expected_failures = [
                'validate-enables-tls',  # LP: #1851610
            ]

            expected_passes.append('validate-uses-keystone')

            if zaza.model.get_relation_id(
                    'neutron-api',
                    'vault',
                    remote_interface_name='certificates'):
                expected_passes.extend(tls_checks)
            else:
                expected_failures.extend(tls_checks)

            expected_to_pass = False

        for unit in zaza.model.get_units(self.application_name,
                                         model_name=self.model_name):
            logging.info('Running `security-checklist` action'
                         ' on  unit {}'.format(unit.entity_id))
            test_utils.audit_assertions(
                zaza.model.run_action(
                    unit.entity_id,
                    'security-checklist',
                    model_name=self.model_name,
                    action_params={}),
                expected_passes,
                expected_failures,
                expected_to_pass=expected_to_pass)


class NeutronNetworkingTest(unittest.TestCase):
    """Ensure that openstack instances have valid networking."""

    RESOURCE_PREFIX = 'zaza-neutrontests'

    @classmethod
    def setUpClass(cls):
        """Run class setup for running Neutron API Networking tests."""
        cls.keystone_session = (
            openstack_utils.get_overcloud_keystone_session())
        cls.nova_client = (
            openstack_utils.get_nova_session_client(cls.keystone_session))
        # NOTE(fnordahl): in the event of a test failure we do not want to run
        # tear down code as it will make debugging a problem virtually
        # impossible.  To alleviate each test method will set the
        # `run_tearDown` instance variable at the end which will let us run
        # tear down only when there were no failure.
        cls.run_tearDown = False

    @classmethod
    def tearDown(cls):
        """Remove test resources."""
        if cls.run_tearDown:
            logging.info('Running teardown')
            for server in cls.nova_client.servers.list():
                if server.name.startswith(cls.RESOURCE_PREFIX):
                    openstack_utils.delete_resource(
                        cls.nova_client.servers,
                        server.id,
                        msg="server")

    def test_instances_have_networking(self):
        """Validate North/South and East/West networking."""
        guest.launch_instance(
            glance_setup.LTS_IMAGE_NAME,
            vm_name='{}-ins-1'.format(self.RESOURCE_PREFIX))
        guest.launch_instance(
            glance_setup.LTS_IMAGE_NAME,
            vm_name='{}-ins-2'.format(self.RESOURCE_PREFIX))

        instance_1 = self.nova_client.servers.find(
            name='{}-ins-1'.format(self.RESOURCE_PREFIX))

        instance_2 = self.nova_client.servers.find(
            name='{}-ins-2'.format(self.RESOURCE_PREFIX))

        def verify(stdin, stdout, stderr):
            """Validate that the SSH command exited 0."""
            self.assertEqual(stdout.channel.recv_exit_status(), 0)

        # Verify network from 1 to 2
        self.validate_instance_can_reach_other(instance_1, instance_2, verify)

        # Verify network from 2 to 1
        self.validate_instance_can_reach_other(instance_2, instance_1, verify)

        # Validate tenant to external network routing
        self.validate_instance_can_reach_router(instance_1, verify)
        self.validate_instance_can_reach_router(instance_2, verify)

        # If we get here, it means the tests passed
        self.run_tearDown = True

    @tenacity.retry(wait=tenacity.wait_exponential(multiplier=1, max=60),
                    reraise=True, stop=tenacity.stop_after_attempt(8))
    def validate_instance_can_reach_other(self,
                                          instance_1,
                                          instance_2,
                                          verify):
        """
        Validate that an instance can reach a fixed and floating of another.

        :param instance_1: The instance to check networking from
        :type instance_1: nova_client.Server

        :param instance_2: The instance to check networking from
        :type instance_2: nova_client.Server
        """
        floating_1 = floating_ips_from_instance(instance_1)[0]
        floating_2 = floating_ips_from_instance(instance_2)[0]
        address_2 = fixed_ips_from_instance(instance_2)[0]

        username = guest.boot_tests['bionic']['username']
        password = guest.boot_tests['bionic'].get('password')
        privkey = openstack_utils.get_private_key(nova_utils.KEYPAIR_NAME)

        openstack_utils.ssh_command(
            username, floating_1, 'instance-1',
            'ping -c 1 {}'.format(address_2),
            password=password, privkey=privkey, verify=verify)

        openstack_utils.ssh_command(
            username, floating_1, 'instance-1',
            'ping -c 1 {}'.format(floating_2),
            password=password, privkey=privkey, verify=verify)

    @tenacity.retry(wait=tenacity.wait_exponential(multiplier=1, max=60),
                    reraise=True, stop=tenacity.stop_after_attempt(8))
    def validate_instance_can_reach_router(self, instance, verify):
        """
        Validate that an instance can reach it's primary gateway.

        We make the assumption that the router's IP is 192.168.0.1
        as that's the network that is setup in
        neutron.setup.basic_overcloud_network which is used in all
        Zaza Neutron validations.

        :param instance: The instance to check networking from
        :type instance: nova_client.Server
        """
        address = floating_ips_from_instance(instance)[0]

        username = guest.boot_tests['bionic']['username']
        password = guest.boot_tests['bionic'].get('password')
        privkey = openstack_utils.get_private_key(nova_utils.KEYPAIR_NAME)

        openstack_utils.ssh_command(
            username, address, 'instance', 'ping -c 1 192.168.0.1',
            password=password, privkey=privkey, verify=verify)
        pass


def floating_ips_from_instance(instance):
    """
    Retrieve floating IPs from an instance.

    :param instance: The instance to fetch floating IPs from
    :type instance: nova_client.Server

    :returns: A list of floating IPs for the specified server
    :rtype: list[str]
    """
    return ips_from_instance(instance, 'floating')


def fixed_ips_from_instance(instance):
    """
    Retrieve fixed IPs from an instance.

    :param instance: The instance to fetch fixed IPs from
    :type instance: nova_client.Server

    :returns: A list of fixed IPs for the specified server
    :rtype: list[str]
    """
    return ips_from_instance(instance, 'fixed')


def ips_from_instance(instance, ip_type):
    """
    Retrieve IPs of a certain type from an instance.

    :param instance: The instance to fetch IPs from
    :type instance: nova_client.Server
    :param ip_type: the type of IP to fetch, floating or fixed
    :type ip_type: str

    :returns: A list of IPs for the specified server
    :rtype: list[str]
    """
    if ip_type not in ['floating', 'fixed']:
        raise RuntimeError(
            "Only 'floating' and 'fixed' are valid IP types to search for"
        )
    return list([
        ip['addr'] for ip in instance.addresses['private']
        if ip['OS-EXT-IPS:type'] == ip_type])
