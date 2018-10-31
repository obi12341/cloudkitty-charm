#!/usr/bin/env python

import subprocess
import sys

from charmhelpers.core.host import (
    restart_on_change,
    service_reload,
)
from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    log,
    open_port,
    relation_ids,
    relation_set,
    unit_get,
)
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.openstack.utils import configure_installation_source
from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    ADMIN,
    INTERNAL,
    PUBLIC,
)
from charmhelpers.fetch import (
    add_source,
    apt_update,
    apt_install,
)

import ck_utils
from ck_context import API_PORTS

hooks = Hooks()
CONFIGS = ck_utils.register_configs()


@hooks.hook('install.real')
def install():
    execd_preinstall()
    configure_installation_source('cloud:bionic-queens')
    add_source('ppa:objectif-libre/cloudkitty-queens')
    apt_update()
    apt_install(ck_utils.determine_packages(), fatal=True)
    for port in API_PORTS.values():
        open_port(port)


@hooks.hook('config-changed')
@restart_on_change(ck_utils.restart_map())
def config_changed():
    CONFIGS.write_all()
    configure_https()

@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    relation_set(relation_id=relation_id,
                 username=config('rabbit-user'), vhost=config('rabbit-vhost'))


@hooks.hook('amqp-relation-changed')
@restart_on_change(ck_utils.restart_map())
def amqp_changed():
    if 'amqp' not in CONFIGS.complete_contexts():
        log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write(ck_utils.CLOUDKITTY_CONF)


@hooks.hook('shared-db-relation-joined')
def db_joined():
    relation_set(cloudkitty_database=config('database'),
                 cloudkitty_username=config('database-user'),
                 cloudkitty_hostname=unit_get('private-address'))


@hooks.hook('shared-db-relation-changed')
@restart_on_change(ck_utils.restart_map())
def db_changed():
    if 'shared-db' not in CONFIGS.complete_contexts():
        log('shared-db relation incomplete. Peer not ready?')
        return
    CONFIGS.write(ck_utils.CLOUDKITTY_CONF)
    subprocess.check_call(['cloudkitty-dbsync', '--config-file',
                           '/etc/cloudkitty/cloudkitty.conf', 'upgrade'])
    subprocess.check_call(['cloudkitty-storage-init', '--config-file',
                           '/etc/cloudkitty/cloudkitty.conf'])


def configure_https():
    CONFIGS.write_all()
    if 'https' in CONFIGS.complete_contexts():
        cmd = ['a2ensite', 'openstack_https_frontend']
    else:
        cmd = ['a2dissite', 'openstack_https_frontend']

    subprocess.check_call(cmd)

    # TODO: improve this by checking if local CN certs are available
    # first then checking reload status (see LP #1433114).
    service_reload('apache2', restart_on_failure=True)

    for rid in relation_ids('identity-service'):
        identity_joined(rid=rid)


@hooks.hook('identity-service-relation-joined')
def identity_joined(rid=None):
    public_url_base = canonical_url(CONFIGS, PUBLIC)
    internal_url_base = canonical_url(CONFIGS, INTERNAL)
    admin_url_base = canonical_url(CONFIGS, ADMIN)

    api_url_template = '%s:8889/'
    public_api_endpoint = (api_url_template % public_url_base)
    internal_api_endpoint = (api_url_template % internal_url_base)
    admin_api_endpoint = (api_url_template % admin_url_base)

    relation_data = {
        'cloudkitty_service': 'cloudkitty',
        'cloudkitty_region': config('region'),
        'cloudkitty_public_url': public_api_endpoint,
        'cloudkitty_admin_url': admin_api_endpoint,
        'cloudkitty_internal_url': internal_api_endpoint,
    }

    relation_set(relation_id=rid, **relation_data)


@hooks.hook('identity-service-relation-changed')
@restart_on_change(ck_utils.restart_map())
def identity_changed():
    if 'identity-service' not in CONFIGS.complete_contexts():
        log('identity-service relation incomplete. Peer not ready?')
        return

    CONFIGS.write_all()
    configure_https()


@hooks.hook('amqp-relation-broken',
            'identity-service-relation-broken',
            'shared-db-relation-broken')
def relation_broken():
    CONFIGS.write_all()


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))


if __name__ == '__main__':
    main()
