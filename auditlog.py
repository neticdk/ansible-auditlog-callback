# This callback plugin will:
#   1) create a logfile (/var/log/ansible/<uuid>.log) for the entire run
#   2) log audit information to that file in JSON-format
#
#   For available settings, see CallbackModule below

import os
import tempfile
import errno
import datetime
import socket
import json
import uuid
import re
import sys

from subprocess import Popen, PIPE
from ansible import utils


def get_dotted_val_in_dict(d, keys):
    """Searches dict d for element in keys.

    Args:
        d (dict): Dictionary to search
        keys (str): String containing element to search for

    Returns:
        Value found at the specified element if found, else None

    Examples:

        Search for the value of foo['bar'] in {'foo': {'bar': 1}}
        >>> get_dotted_val_in_dict({'foo': {'bar': 1}}, 'foo.bar')
        1

        Search for the value of foo['baz'] in {'foo': {'bar': 1}}
        >>> get_dotted_val_in_dict({'foo': {'bar': 1}}, 'foo.baz')
    """

    if "." in keys:
        key, rest = keys.split(".", 1)
        dval = d.get(key, {})
        if isinstance(dval, dict):
            return get_dotted_val_in_dict(dval, rest)
    else:
        if d.get(keys):
            return d[keys]


def truthy_string(s):
    """Determines if a string has a truthy value"""
    return str(s).lower() in ['true', '1', 'y', 'yes']


class JsonAuditLogger(object):
    """Writes auditlog entries to a file in JSON format.

    All log entries are marked with the same UUID and have timestamps.
    """

    def __init__(self, logdir='/var/log/ansible'):
        self.uuid = str(uuid.uuid4())
        self.hostname = socket.gethostname()

        try:
            if not self.isWritable(logdir):
                raise Exception("Access denied to {}".format(logdir))
        except Exception:
            raise

        self.logfile = os.path.join(logdir, "{}.log".format(self.uuid))

    def isWritable(self, path):
        try:
            testfile = tempfile.TemporaryFile(dir=path)
            testfile.close()
        except OSError as e:
            if e.errno == errno.EACCES:  # 13
                return False
            e.filename = path
            raise
        return True

    def log(self, event_id, log_entry={}):
        log_entry['event'] = event_id
        log_entry['timestamp'] = datetime.datetime.now().isoformat()
        log_entry['controlhost'] = self.hostname
        log_entry['uuid'] = self.uuid

        data = json.dumps(log_entry, sort_keys=True)

        with open(self.logfile, 'a') as f:
            f.write(data+'\n')


class CallbackModule(object):
    """Logs audit information about ansible runs.

    Throws a warning if logging fails.

    Settings (environment variables):

        ANSIBLE_AUDITLOG_DISABLED:
            - enables or disables auditlog
            - values: true|false
            - default: false

        ANSIBLE_AUDITLOG_FAILMODE:
            - wether to fail or warn if logging doesn't work
            - values: warn|fail
            - default: warn

        ANSIBLE_AUDITLOG_LOGNAME_ENABLED:
            - enables or disables the use of 'logname' to find out who
              originally ran ansible
            - values: true|false
            - default: true

        ANSIBLE_AUDITLOG_LOGDIR:
            - sets the directory used to store log files
            - default: /var/log/ansible

        ANSIBLE_AUDITLOG_AUDIT_VARS:
            - sets a list of variables that should have their values logged
            - format: comma-separated list of variable names. For dicts use dots
              in the names to indicate the dict level.
            - default: None
    """

    def __init__(self):
        self.disabled = truthy_string(os.getenv('ANSIBLE_AUDITLOG_DISABLED', 0))
        self.log_logname = truthy_string(
            os.getenv('ANSIBLE_AUDITLOG_LOGNAME_ENABLED', 1))
        logdir = os.getenv('ANSIBLE_AUDITLOG_LOGDIR', '/var/log/ansible')
        audit_vars = os.getenv('ANSIBLE_AUDITLOG_AUDIT_VARS', None)
        fail_mode = os.getenv('ANSIBLE_AUDITLOG_FAILMODE', 'warn')

        if self.disabled:
            utils.warning('Auditlog has been disabled!')
            return None

        # Example: version,my.nested.var
        if audit_vars:
            # Only allow alphanumeric + _ + .
            pattern = re.compile('[^\w.,]+', re.UNICODE)
            self.audit_vars = pattern.sub('', audit_vars).split(',')
            # convert to dict
            self.audit_vars = dict((el, 0) for el in self.audit_vars)
        else:
            self.audit_vars = {}

        try:
            self.logger = JsonAuditLogger(logdir=logdir)
        except Exception as e:
            msg = 'Unable to initialize audit logging: {}'.format(str(e))
            self.disabled = True
            utils.warning(msg)
            if fail_mode == 'fail':
                print(str(e))
                sys.exit(1)

    def on_any(self, *args, **kwargs):
        pass

    def runner_on_failed(self, host, res, ignore_errors=False):
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_failed', {
            'inventory_host': host,
            'module_name': module_name,
            'ignore_errors': ignore_errors,
            'msg': res.get('msg', '')
        })

    def runner_on_ok(self, host, res):
        changed = 'changed' if res.get('changed', False) else 'ok'
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_ok', {
            'inventory_host': host,
            'status': changed,
            'module_name': module_name
            })

    def runner_on_skipped(self, host, item=None):
        pass

    def runner_on_error(self, host, msg):
        self.logger.log('runner_on_error', {
            'inventory_host': host,
            'msg': msg,
            })

    def runner_on_unreachable(self, host, res):
        self.logger.log('runner_on_unreachable', {
            'inventory_host': host,
            })

    def runner_on_no_hosts(self):
        pass

    def runner_on_async_poll(self, host, res, jid, clock):
        pass

    def runner_on_async_ok(self, host, res, jid):
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_async_ok', {
            'inventory_host': host,
            'module_name': module_name,
            })

    def runner_on_async_failed(self, host, res, jid):
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_async_failed', {
            'inventory_host': host,
            'module_name': module_name,
            })

    def runner_on_file_diff(self, host, diff):
        pass

    def playbook_on_start(self):
        # These are not used until `playbook_on_play_start`
        self.my_vars = self.playbook.global_vars
        self.my_vars = utils.combine_vars(
            self.my_vars, self.playbook.extra_vars)

        # This gets us the user that originally spawed the ansible process.
        # Watch out: On Linux, if you (yes, you) started some process that
        # starts ansible (i.e. jenkins), then you (yes, you) will be the one
        # listed as the user running ansible, even though you started jenkins
        # indirectly (e.g. using "sudo service jenkins start").
        if self.log_logname:
            p = Popen(['logname'], stdout=PIPE)
            logname = p.stdout.readline().rstrip('\n')
            p.terminate()
        else:
            logname = None

        log_entry = {
            'playbook': self.playbook.filename,
            'hosts': self.playbook.inventory.list_hosts(),
            'inventory': self.playbook.inventory.host_list,
            'only_tags': self.playbook.only_tags,
            'skip_tags': self.playbook.skip_tags,
            'check_mode': self.playbook.check,
            'automation_on_behalf_of': self.playbook.extra_vars.get(
                'automation_on_behalf_of', ''),
            'remote_user': self.playbook.remote_user,
            'su': getattr(self.playbook, 'su', None),
            'su_user': getattr(self.playbook, 'su_user', None),
            'sudo': getattr(self.playbook, 'sudo', None),
            'sudo_user': getattr(self.playbook, 'sudo_user', None),
            'become': getattr(self.playbook, 'become', None),
            'become_method': getattr(self.playbook, 'become_method', None),
            'become_user': getattr(self.playbook, 'become_user', None),
            'USER': os.getenv('USER'),
            'SUDO_USER': os.getenv('SUDO_USER'),
            'logname': logname,
        }

        self.logger.log('playbook_on_start', log_entry)

    def playbook_on_notify(self, host, handler):
        pass

    def playbook_on_no_hosts_matched(self):
        pass

    def playbook_on_no_hosts_remaining(self):
        pass

    def playbook_on_task_start(self, name, is_conditional):
        self.logger.log('playbook_on_task_start', {
            'name': name,
            })

    def playbook_on_vars_prompt(
            self, varname, private=True, prompt=None, encrypt=None,
            confirm=False, salt_size=None, salt=None, default=None):
        pass

    def playbook_on_setup(self):
        pass

    def playbook_on_import_for_host(self, host, imported_file):
        pass

    def playbook_on_not_import_for_host(self, host, missing_file):
        pass

    def playbook_on_play_start(self, pattern):
        self.inventory = self.playbook.inventory

        # Don't log empty plays
        hosts_in_play = self.inventory.list_hosts(self.play.hosts)
        if len(hosts_in_play) == 0:
            return

        # Combine inventory vars, global vars and extra vars
        self.my_vars = utils.combine_vars(self.my_vars, self.play.vars)

        # This are not used until `playbook_on_stats`
        for myvar in self.audit_vars:
            val = get_dotted_val_in_dict(self.my_vars, myvar)
            self.audit_vars[myvar] = val

        self.logger.log('playbook_on_play_start', {
            'name': self.play.name,
            'remote_user': self.play.remote_user,
            'su': getattr(self.play, 'su', None),
            'su_user': getattr(self.play, 'su_user', None),
            'sudo': getattr(self.play, 'sudo', None),
            'sudo_user': getattr(self.play, 'sudo_user', None),
            'become': getattr(self.play, 'become', None),
            'become_method': getattr(self.play, 'become_method', None),
            'become_user': getattr(self.play, 'become_user', None),
            'serial': self.play.serial,
            'max_fail_percentage': self.play.max_fail_pct,
            'hosts': self.play.hosts,
            })

    def playbook_on_stats(self, stats):
        stats_keys = ['processed', 'failures', 'ok', 'dark', 'changed',
                      'skipped']
        summary_stats_keys = ['failures', 'ok', 'unreachable', 'changed',
                              'skipped']
        log_entry = {'stats': {'summary': {}, 'details': {}}}

        for key in stats_keys:
            log_entry['stats']['details'][key] = {}
            log_entry['stats']['details'][key] = getattr(stats, key)

        for key in summary_stats_keys:
            log_entry['stats']['summary'][key] = 0

        hosts = sorted(stats.processed.keys())
        for h in hosts:
            s = stats.summarize(h)
            for key in summary_stats_keys:
                log_entry['stats']['summary'][key] += s[key]

        log_entry['audit_vars'] = self.audit_vars

        self.logger.log('playbook_on_stats', log_entry)
