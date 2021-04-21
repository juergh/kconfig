#!/usr/bin/env python3
#
# A simple kernel Kconfig parser
#

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
        line = line.strip()

        # Make sure that lines ending with \ continue
        if cont:
            line = prev_line + ' ' + line
        if line.endswith('\\'):
            cont = True
            prev_line = line[:-1].strip()
            continue
        cont = False

        yield line

class Kconfig():
    def __init__(self, ksource, kconfig, arch, debug=False):
        self.ksource = ksource
        self.kconfig = kconfig
        self.arch = arch
        self.debug = debug

        # The found config options
        self.configs = {}

        # The list of all makefiles
        self.makefiles = None

        # Read and parse the Kconfig tree
        self._kconfigs = {}
        self._read_kconfig(self.kconfig)

    def _find_makefiles(self):
        """
        Find all Makefiles and Kbuild files
        """
        if self.debug:
            print('-- Find Makefiles an Kbuild files')

        result = []
        for path, _dirs, files in os.walk(self.ksource):
            for f in files:
                if f in ('Makefile', 'Kbuild'):
                    result.append(os.path.join(path, f))
        return result

    def module_to_config(self, module):
        """
        Return the config option that enables the provided kernel module
        """
        if not self.makefiles:
            self.makefiles = self._find_makefiles()

        for m in self.makefiles:
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
        if not os.path.exists(kconfig):
            return

        # Prevent reading the same Kconfig multiple times
        if kconfig in self._kconfigs:
            return
        self._kconfigs[kconfig] = 1

        # Assemble the full kconfig file path and replace environment variables
        source = os.path.join(self.ksource, kconfig)
        source = source.replace('$(SRCARCH)', SRCARCH[self.arch])

        if self.debug:
            print('-- Read Kconfig {}'.format(source))
        with open(source) as fh:
            state = 'NONE'
            config = None

            for line in read_line(fh):
                # Collect any Kconfig sources
                m = re.match(r'^source\s+"?([^"]+)', line)
                if m:
                    state = 'NONE'
                    config = None
                    self._read_kconfig(m.group(1))
                    continue

                # Block boundary
                if re.match(r'^(comment|choice|endchoice|if|endif|endmenu)\b',
                            line):
                    state = 'NONE'
                    config = None
                    continue

                # Config found
                m = re.match(r'^\s*(menu)?config\s+(\S+)\s*$', line)
                if m:
                    state = 'CONFIG'
                    config = m.group(2)
                    # Initialize the config data hash
                    if config not in self.configs:
                        self.configs[config] = {
                            'kconfig': [],
                            'help': [],
                            'depends': [],
                        }
                    # Add the Kconfig file that references this option
                    self.configs[config]['kconfig'].append(kconfig)
                    continue

                if state == 'CONFIG':
                    # Config 'help' found
                    if re.match(r'^\s*(---)?help(---)?\s*$', line):
                        state = 'CONFIG_HELP'
                        continue

                    # Config 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        self.configs[config]['depends'].append(m.group(1))
                        continue

                    continue

                # Collect (non-empty) config help lines
                if state == 'CONFIG_HELP' and line.strip():
                    self.configs[config]['help'].append(line.strip())
                    continue
