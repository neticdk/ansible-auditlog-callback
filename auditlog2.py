# This callback plugin will:
#   1) create a logfile (/var/log/ansible/<uuid>.log) for the entire run
#   2) log audit information to that file in JSON-format
#
#   For available settings, see CallbackModule below
#
# This callback is ansible 2.1+ only

import os
import tempfile
import errno
import datetime
import socket
import json
import uuid
import re
import pwd
import sys

try:
    from __main__ import display as global_display
except ImportError:
    from ansible.utils.display import Display
    global_display = Display()

from ansible.plugins.callback import CallbackBase


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


class CallbackModule(CallbackBase):
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

    CALLBACK_VERSION = 2.1
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'auditlog2'
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self, display=None):
        super(CallbackModule, self).__init__()

        if display:
            self._display = display
        else:
            self._display = global_display

        self.disabled = truthy_string(os.getenv('ANSIBLE_AUDITLOG_DISABLED', 0))
        logdir = os.getenv('ANSIBLE_AUDITLOG_LOGDIR', '/var/log/ansible')
        audit_vars = os.getenv('ANSIBLE_AUDITLOG_AUDIT_VARS', None)
        fail_mode = os.getenv('ANSIBLE_AUDITLOG_FAILMODE', 'warn')

        if self.disabled:
            self._display.warning('Auditlog has been disabled!')
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
            self._display.warning(msg)
            if fail_mode == 'fail':
                print(str(e))
                sys.exit(1)

    def set_play_context(self, play_context):
        self.play_context = play_context

    def runner_on_ok(self, host, res):
        changed = 'changed' if res.get('changed', False) else 'ok'
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_ok', {
            'inventory_host': host,
            'status': changed,
            'module_name': module_name
            })

    def runner_on_failed(self, host, res, ignore_errors=False):
        module_name = res.get('invocation', {}).get('module_name', '')
        self.logger.log('runner_on_failed', {
            'inventory_host': host,
            'module_name': module_name,
            'ignore_errors': ignore_errors,
            'msg': res.get('msg', '')
        })

    def runner_on_error(self, host, msg):
        self.logger.log('runner_on_error', {
            'inventory_host': host,
            'msg': msg,
            })

    def runner_on_unreachable(self, host, res):
        self.logger.log('runner_on_unreachable', {
            'inventory_host': host,
            })

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

    def v2_playbook_on_start(self, playbook):
        try:
            from __main__ import cli
            self.options = vars(cli.options)
        except ImportError:
            self.options = {}

        self.playbook = playbook
        self.inventory = self.options.get('inventory', '')

        # Load variables from first play
        firstplay = playbook.get_plays()[0]
        vm = firstplay.get_variable_manager()
        self.vars = vm.get_vars(firstplay.get_loader(), play=firstplay)

        # If the playbook was run by some sort of automation that somebody else
        # triggered, this extra var can be used to tell us about it.
        automation_on_behalf_of = self.vars.get('automation_on_behalf_of', '')

        # Build a list of hosts in all plays
        # This relies on internal ansible stuff that might some day not work
        hosts = []
        for play in playbook.get_plays():
            host_list = play.get_variable_manager()._inventory.list_hosts()
            play_hosts = [h.name for h in host_list if h.name not in hosts]
            hosts.extend(play_hosts)

        try:
            user = os.getlogin()
        except OSError:
            user = pwd.getpwuid(os.geteuid())[0]

        log_entry = {
            'playbook': playbook._file_name,
            'hosts': hosts,
            'inventory': self.inventory,
            'options': self.options,
            'USER': os.getenv('USER'),
            'SUDO_USER': os.getenv('SUDO_USER'),
            'realuser': user,
            'automation_on_behalf_of': automation_on_behalf_of,
        }

        self.logger.log('playbook_on_start', log_entry)

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.logger.log('playbook_on_task_start', {
            'name': task.get_name(),
        })

    def v2_playbook_on_play_start(self, play):
        # Don't log empty plays
        hosts_in_play = play.hosts
        if len(hosts_in_play) == 0:
            return

        vm = play.get_variable_manager()
        play_vars = vm.get_vars(play.get_loader(), play=play)

        # This are not used until `playbook_on_stats`
        for v in self.audit_vars:
            val = get_dotted_val_in_dict(play_vars, v)
            self.audit_vars[v] = val

        self.logger.log('playbook_on_play_start', {
            'name': play.name,
            'remote_user': self.play_context.remote_user,
            'become': self.play_context.become,
            'become_method': self.play_context.become_method,
            'become_user': self.play_context.become_user,
            'serial': play.serial,
            'max_fail_percentage': play.max_fail_percentage,
            'hosts': play.hosts,
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
