import os
import tempfile
import errno
from subprocess import *
import datetime
import socket
import json
import uuid
from ansible import utils

class AnsibleAuditlogLogger(object):
    """
    writes the log entries
    """

    def __init__(self, logdir='/var/log/ansible'):
        self.uuid = str(uuid.uuid4())
        self.hostname = socket.gethostname()
        self.logdir = logdir

        try:
            if not self.isWritable(logdir):
                raise Exception("Access denied to {}".format(logdir))
        except Exception as e:
            raise

        self.logfile = os.path.join(self.logdir, "{}.log".format(self.uuid))

    def isWritable(self, path):
        try:
            testfile = tempfile.TemporaryFile(dir = path)
            testfile.close()
        except OSError as e:
            if e.errno == errno.EACCES: # 13
                return False
            e.filename = path
            raise
        return True

    def log(self, event, log_entry={}):
        log_entry['event'] = event
        log_entry['timestamp'] = datetime.datetime.now().isoformat()
        log_entry['controlhost'] = self.hostname
        log_entry['uuid'] = self.uuid

        data = json.dumps(log_entry, sort_keys=True)

        fd = open(self.logfile, 'a')
        fd.write(data+'\n')
        fd.close()



class CallbackModule(object):
    """
    writes log entries about runs
    """

    def __init__(self):
        if str(os.getenv('ANSIBLE_AUDITLOG_DISABLED', 0)).lower() in ['true', '1', 'y', 'yes']:
            utils.warning('Auditlog has been disabled!')
            self.disabled = True
        else:
            self.disabled = False

        try:
            self.logger = AnsibleAuditlogLogger()
        except Exception as e:
            self.disabled = True
            utils.warning('Unable to initialize logging. Auditlog disabled. Error: {}'.format(str(e)))

    def on_any(self, *args, **kwargs):
        pass

    def runner_on_failed(self, host, res, ignore_errors=False):
        module_name = res['invocation'].get('module_name', '') if 'invocation' in res else ''
        self.logger.log('runner_on_failed', {
            'inventory_host': host,
            'module_name': module_name,
            'ignore_errors': ignore_errors,
            'msg': res.get('msg', ''),
            })

    def runner_on_ok(self, host, res):
        changed = 'changed' if res.get('changed', False) else 'ok'
        module_name = res['invocation'].get('module_name', '') if 'invocation' in res else ''
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
        module_name = res['invocation'].get('module_name', '') if 'invocation' in res else ''
        self.logger.log('runner_on_async_ok', {
            'inventory_host': host,
            'module_name': module_name,
            })

    def runner_on_async_failed(self, host, res, jid):
        module_name = res['invocation'].get('module_name', '') if 'invocation' in res else ''
        self.logger.log('runner_on_async_failed', {
            'inventory_host': host,
            'module_name': module_name,
            })

    def runner_on_file_diff(self, host, diff):
        pass

    def playbook_on_start(self):
        p = Popen(['logname'], stdout=PIPE)
        logname = p.stdout.readline().rstrip('\n')
        p.terminate()

        log_entry = {
            'playbook': self.playbook.filename,
            'hosts': self.playbook.inventory.list_hosts(),
            'inventory': self.playbook.inventory.host_list,
            'only_tags': self.playbook.only_tags,
            'skip_tags': self.playbook.skip_tags,
            'check_mode': self.playbook.check,
            'automation_on_behalf_of': self.playbook.extra_vars.get('automation_on_behalf_of', ''),
            'remote_user': self.playbook.remote_user,
            'su': self.playbook.su,
            'su_user': self.playbook.su_user,
            'sudo': self.playbook.sudo,
            'sudo_user': self.playbook.sudo_user,
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

        self.logger.log('playbook_on_play_start', {
            'name': self.play.name,
            'remote_user': self.play.remote_user,
            'sudo': self.play.sudo,
            'sudo_user': self.play.sudo_user,
            'su': self.play.su,
            'su_user': self.play.su_user,
            'serial': self.play.serial,
            'max_fail_percentage': self.play.max_fail_pct,
            'hosts': self.play.hosts,
            })

    def playbook_on_stats(self, stats):
        log_entry= {'stats': {}}

        for stat in ['processed', 'failures', 'ok', 'dark', 'changed', 'skipped']:
            log_entry['stats'][stat] = getattr(stats, stat)

        self.logger.log('playbook_on_stats', log_entry)
