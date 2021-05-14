#!/usr/bin/env python3
#
# A simple kernel Kconfig parser
#

import logging
import os
import re

# Mapping between Debian package and kernel source architecture names
SRCARCH = {
    # Debian arches
    'amd64': 'x86',
    'arm64': 'arm64',
    'armhf': 'arm',
    'i386': 'x86',
    's390x': 's390',

    # Kernel arches
    's390': 's390',
    'x86': 'x86',
}

def read_line(fh):
    """
    Return complete (continued) lines
    """
    prev_line = ''
    cont = False

    for line in fh:
        # Strip newlines and such
        line = line.rstrip()

        # Make sure that lines ending with \ continue
        if cont:
            line = prev_line + ' ' + line.lstrip()
        if line.endswith('\\'):
            cont = True
            prev_line = line[:-1].rstrip()
            continue
        cont = False

        yield line.replace('\t', ' ' * 8)

class Kconfig():
    def __init__(self, ksource, kconfig, arch, log_level=logging.INFO):
        self.ksource = ksource
        self.kconfig = kconfig
        self.arch = arch

        # Setup the logger
        logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=log_level)
        self.log = logging.getLogger(__name__)

        # The found config options
        self.configs = {}

        # The list of all makefiles
        self._makefiles = []

        # 'if' conditions
        self._if = []

        # 'menu' conditions
        self._menu = []

        # 'choice' conditions
        self._choice = []

        # Read and parse the Kconfig tree
        self._kconfigs = {}
        self._read_kconfig(self.kconfig)

    def _log_line(self, token, line):
        self.log.debug('%20s : %s', token, line)

    def _find_makefiles(self):
        """
        Find all Makefiles and Kbuild files
        """
        self.log.debug('Find Makefiles an Kbuild files')

        result = []
        for path, _dirs, files in os.walk(self.ksource):
            rel_path = os.path.relpath(path, self.ksource)
            for f in files:
                if f in ('Makefile', 'Kbuild'):
                    result.append(os.path.join(rel_path, f))
        return result

    def module_to_config(self, module):
        """
        Return the config option that enables the provided kernel module
        """
        if not self._makefiles:
            self._makefiles = self._find_makefiles()

        for m in self._makefiles:
            with open(os.path.join(self.ksource, m)) as fh:
                for line in read_line(fh):
                    m = re.match(r'obj-\$\(CONFIG_([^\)]+)\)\s*[+:]?=\s*(.*)',
                                 line)
                    if m:
                        for o in m.group(2).split(' '):
                            if o in (module + '.o',
                                     module.replace('_', '-') + '.o',
                                     module.replace('-', '_') + '.o'):
                                return m.group(1)
        return None

    def _read_kconfig(self, kconfig):
        """
        Read (and parse) the provided kconfig file and recursively traverse all
        included kconfig files as well.
        """
        # Prevent reading the same Kconfig multiple times
        if kconfig in self._kconfigs:
            return
        self._kconfigs[kconfig] = 1

        # Assemble the full kconfig file path and replace environment variables
        source = os.path.join(self.ksource, kconfig)
        source = source.replace('$(SRCARCH)', SRCARCH[self.arch])
        source = source.replace('$SRCARCH', SRCARCH[self.arch])

        with open(source) as fh:
            state = 'NONE'
            help_indent = ''
            config = None

            for line in read_line(fh):
                # Determine the line indentation
                line_indent = re.match(r'^(\s*)', line).group(1)

                # -------------------------------------------------------------
                # Collect config help lines
                if state == 'CONFIG_HELP':
                    if not help_indent and line:
                        # Store the indent of the first help line
                        help_indent = line_indent
                    if help_indent:
                        if line.startswith(help_indent) or not line:
                            self._log_line('[config:help_text]', line)
                            self.configs[config]['help'].append(line[len(help_indent):])
                            continue

                        # End of help, remove trailing empty lines
                        while not self.configs[config]['help'][-1]:
                            del self.configs[config]['help'][-1]
                        state = 'NONE'

                # -------------------------------------------------------------
                # Ignore comments
                if re.match(r'^\s*(#|comment\s+"[^"]+")', line):
                    self._log_line('[comment]', line)
                    continue

                # -------------------------------------------------------------
                # Variable assignment
                if re.match(r'^\S+\s+(:)?=', line):
                    self._log_line('[variable]', line)
                    continue

                # -------------------------------------------------------------
                # Macro definition
                if re.match(r'^\$\(.+\)', line):
                    self._log_line('[macro]', line)
                    continue

                # -------------------------------------------------------------
                # Collect any Kconfig sources
                m = re.match(r'^\s*source\s+"([^"]+)"', line)
                if m:
                    self._log_line('[source]', line)
                    state = 'NONE'
                    self._read_kconfig(m.group(1))
                    continue

                # -------------------------------------------------------------
                # 'if' statement
                m = re.match(r'^if\s+(.*)$', line)
                if m:
                    self._log_line('[if]', line)
                    state = 'NONE'
                    self._if.append(m.group(1))
                    continue

                # 'endif' statement
                if re.match(r'^endif\b', line):
                    self._log_line('[endif]', line)
                    state = 'NONE'
                    self._if.pop()
                    continue

                # -------------------------------------------------------------
                # 'menu' found
                m = re.match(r'^\s*(main)?menu\s+"([^"]+)"', line)
                if m:
                    self._log_line('[menu]', line)
                    state = 'MENU'
                    self._menu.append({
                        'menu': m.group(2),
                        'depends': [],
                    })
                    continue

                # 'endmenu' found
                if re.match(r'^endmenu\b', line):
                    self._log_line('[endmenu]', line)
                    state = 'NONE'
                    self._menu.pop()
                    continue

                if state == 'MENU':
                    # Menu 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        self._log_line('[menu:depends_on]', line)
                        self._menu[-1]['depends'].append(m.group(1))
                        continue

                # -------------------------------------------------------------
                # 'choice' found
                if re.match(r'^choice\b', line):
                    self._log_line('[choice]', line)
                    state = 'CHOICE'
                    self._choice.append({
                        'prompt': '',
                        'depends': [],
                        'default': [],
                    })
                    continue

                # 'endchoice' found
                if re.match(r'^endchoice\b', line):
                    self._log_line('[endchoice]', line)
                    state = 'NONE'
                    self._choice.pop()
                    continue

                if state == 'CHOICE':
                    # Choice 'prompt' found
                    m = re.match(r'^\s+prompt\s+"([^"]+)"', line)
                    if m:
                        self._log_line('[choice:prompt]', line)
                        self._choice[-1]['prompt'] = m.group(1)
                        continue

                    # Choice 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        self._log_line('[choice:depends_on]', line)
                        self._choice[-1]['depends'].append(m.group(1))
                        continue

                    # Choice 'default' found
                    m = re.match(r'^\s*default\s+(.*)$', line)
                    if m:
                        self._log_line('[choice:default]', line)
                        self._choice[-1]['default'].append(m.group(1))
                        continue

                # -------------------------------------------------------------
                # Config found
                m = re.match(r'^\s*(menu)?config\s+([0-9a-zA-Z_]+)', line)
                if m:
                    self._log_line('[config]', line)
                    state = 'CONFIG'
                    config = m.group(2)
                    # Initialize the config data hash
                    if config not in self.configs:
                        self.configs[config] = {
                            'kconfig': [],
                            'help': [],
                            'depends': [],
                            'select': [],
                            'if': self._if.copy(),
                            'menu': self._menu.copy(),
                            'choice': self._choice.copy(),
                        }
                    # Add the Kconfig file that references this option
                    self.configs[config]['kconfig'].append(kconfig)
                    continue

                if state == 'CONFIG':
                    # Config 'help' found
                    if re.match(r'^\s*(---)?help(---)?\s*$', line):
                        self._log_line('[config:help]', line)
                        state = 'CONFIG_HELP'
                        help_indent = ''
                        continue

                    # Config 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        self._log_line('[config:depends_on]', line)
                        self.configs[config]['depends'].append(m.group(1))
                        continue

                    # Config 'select' found
                    m = re.match(r'^\s*select\s+(.*)$', line)
                    if m:
                        self._log_line('[config:select]', line)
                        self.configs[config]['select'].append(m.group(1))
                        continue

                    continue

                # -------------------------------------------------------------
                # Sanity checks
                if re.match(r'\s*source\s+', line):
                    self.log.warning('[bug] : %s', line)

                # -------------------------------------------------------------
                # Unprocessed lines
                if line:
                    self._log_line('[ignored]', line)
