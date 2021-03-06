# Copyright 2013: Mirantis Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

""" Rally command: deployment """

from __future__ import print_function

import json
import os
import sys

import jsonschema
import yaml

from rally.cmd import cliutils
from rally.cmd.commands import use
from rally.cmd import envutils
from rally import db
from rally import exceptions
from rally.i18n import _
from rally.objects import endpoint
from rally.openstack.common import cliutils as common_cliutils
from rally.orchestrator import api
from rally import osclients
from rally import utils


class DeploymentCommands(object):
    """Set of commands that allow you to manage deployments."""

    @cliutils.args('--name', type=str, required=True,
                   help='A name of the deployment.')
    @cliutils.args('--fromenv', action='store_true',
                   help='Read environment variables instead of config file')
    @cliutils.args('--filename', type=str, required=False,
                   help='A path to the configuration file of the '
                   'deployment.')
    @cliutils.args('--no-use', action='store_false', dest='do_use',
                   help='Don\'t set new deployment as default for'
                        ' future operations')
    def create(self, name, fromenv=False, filename=None, do_use=False):
        """Create new deployment.

        This command will create new deployment record in rally database.
        In case of ExistingCloud deployment engine it will use cloud,
        represented in config.
        In cases when cloud doesn't exists Rally will deploy new one
        for you with Devstack or Fuel. For this purposes different deployment
        engines are developed.

        If you use ExistionCloud deployment engine you can pass deployment
        config by environment variables:
            OS_USERNAME
            OS_PASSWORD
            OS_AUTH_URL
            OS_TENANT_NAME

        All other deployment engines need more complex configuration data, so
        it should be stored in configuration file.

        You can use physical servers, lxc containers, KVM virtual machines
        or virtual machines in OpenStack for deploying the cloud in.
        Except physical servers, Rally can create cluster nodes for you.
        Interaction with virtualisation software, OpenStack
        cloud or physical servers is provided by server providers.

        :param fromenv: boolean, read environment instead of config file
        :param filename: a path to the configuration file
        :param name: a name of the deployment
        """

        if fromenv:
            required_env_vars = ["OS_USERNAME", "OS_PASSWORD", "OS_AUTH_URL",
                                 "OS_TENANT_NAME"]

            unavailable_vars = [v for v in required_env_vars
                                if v not in os.environ]
            if unavailable_vars:
                print("The following environment variables are required but "
                      "not set: %s" % ' '.join(unavailable_vars))
                return(1)

            config = {
                "type": "ExistingCloud",
                "auth_url": os.environ['OS_AUTH_URL'],
                "admin": {
                    "username": os.environ['OS_USERNAME'],
                    "password": os.environ['OS_PASSWORD'],
                    "tenant_name": os.environ['OS_TENANT_NAME']
                }
            }
            region_name = os.environ.get('OS_REGION_NAME')
            if region_name and region_name != 'None':
                config['region_name'] = region_name
        else:
            if not filename:
                print("Either --filename or --fromenv is required")
                return(1)
            filename = os.path.expanduser(filename)
            with open(filename, 'rb') as deploy_file:
                config = yaml.safe_load(deploy_file.read())

        try:
            deployment = api.create_deploy(config, name)
        except jsonschema.ValidationError:
            print(_("Config schema validation error: %s.") % sys.exc_info()[1])
            return(1)

        self.list(deployment_list=[deployment])
        if do_use:
            use.UseCommands().deployment(deployment['uuid'])

    @cliutils.args('--uuid', dest='deploy_id', type=str, required=False,
                   help='UUID of a deployment.')
    @envutils.with_default_deploy_id
    def recreate(self, deploy_id=None):
        """Destroy and create an existing deployment.

        Unlike 'deployment destroy' command deployment database record will
        not be deleted, so deployment's UUID stay same.

        :param deploy_id: a UUID of the deployment
        """
        api.recreate_deploy(deploy_id)

    @cliutils.args('--uuid', dest='deploy_id', type=str, required=False,
                   help='UUID of a deployment.')
    @envutils.with_default_deploy_id
    def destroy(self, deploy_id=None):
        """Destroy existing deployment.

        This will delete all containers, virtual machines, OpenStack instances
        or Fuel clusters created during Rally deployment creation. Also it will
        remove deployment record from Rally database.

        :param deploy_id: a UUID of the deployment
        """
        api.destroy_deploy(deploy_id)

    def list(self, deployment_list=None):
        """List existing deployments."""

        headers = ['uuid', 'created_at', 'name', 'status', 'active']
        current_deploy_id = envutils.get_global('RALLY_DEPLOYMENT')
        deployment_list = deployment_list or db.deployment_list()

        table_rows = []
        if deployment_list:
            for t in deployment_list:
                r = [str(t[column]) for column in headers[:-1]]
                r.append("" if t["uuid"] != current_deploy_id else "*")
                table_rows.append(utils.Struct(**dict(zip(headers, r))))
            common_cliutils.print_list(table_rows, headers,
                                       sortby_index=headers.index(
                                           'created_at'))
        else:
            print(_("There are no deployments. "
                    "To create a new deployment, use:"
                    "\nrally deployment create"))

    @cliutils.args('--uuid', dest='deploy_id', type=str, required=False,
                   help='UUID of a deployment.')
    @envutils.with_default_deploy_id
    def config(self, deploy_id=None):
        """Display configuration of the deployment.

        Output is the configuration of the deployment in a
        pretty-printed JSON format.

        :param deploy_id: a UUID of the deployment
        """
        deploy = db.deployment_get(deploy_id)
        result = deploy['config']
        print(json.dumps(result, sort_keys=True, indent=4))

    @cliutils.args('--uuid', dest='deploy_id', type=str, required=False,
                   help='UUID of a deployment.')
    @envutils.with_default_deploy_id
    def show(self, deploy_id=None):
        """Show the endpoints of the deployment.

        :param deploy_id: a UUID of the deployment
        """

        headers = ['auth_url', 'username', 'password', 'tenant_name',
                   'region_name', 'endpoint_type', 'admin_port']
        table_rows = []

        deployment = db.deployment_get(deploy_id)
        users = deployment.get("users", [])
        admin = deployment.get("admin")
        endpoints = users + [admin] if admin else users

        for ep in endpoints:
            data = [ep.get(m, '') for m in headers]
            table_rows.append(utils.Struct(**dict(zip(headers, data))))
        common_cliutils.print_list(table_rows, headers)

    @cliutils.args('--uuid', dest='deploy_id', type=str, required=False,
                   help='UUID of a deployment.')
    @envutils.with_default_deploy_id
    def check(self, deploy_id=None):
        """Check keystone authentication and list all available services.

        :param deploy_id: a UUID of the deployment
        """

        headers = ['services', 'type', 'status']
        table_rows = []
        try:
            admin = db.deployment_get(deploy_id)['admin']
            # TODO(boris-42): make this work for users in future
            for endpoint_dict in [admin]:
                clients = osclients.Clients(endpoint.Endpoint(**endpoint_dict))
                client = clients.verified_keystone()
                print("keystone endpoints are valid and following "
                      "services are available:")
                for service in client.services.list():
                    data = [service.name, service.type, 'Available']
                    table_rows.append(utils.Struct(**dict(zip(headers, data))))
        except exceptions.InvalidArgumentsException:
            data = ['keystone', 'identity', 'Error']
            table_rows.append(utils.Struct(**dict(zip(headers, data))))
            print(_("Authentication Issues: %s.")
                  % sys.exc_info()[1])
            return(1)
        common_cliutils.print_list(table_rows, headers)
