#!/usr/bin/env python3
#
# A simple kernel Kconfig parser
#

import json
import os
import re
import sys

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

class Kconfig():
    def __init__(self, ksource, kconfig, arch, debug=False):
        self.ksource = ksource
        self.kconfig = kconfig
        self.arch = arch
        self.debug = debug

        # The found config options
        self.configs = {}

        # Read and parse the Kconfig tree
        self._kconfigs = {}
        self._read_kconfig(self.kconfig)

    def _read_kconfig(self, kconfig):
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
            prev_line = ''
            cont = False
            state = 'NONE'

            for line in fh:
                # Strip newlines and such
                line = line.rstrip()

                # Make sure that lines ending with \ continue
                if cont:
                    line = prev_line + ' ' + line
                if line.endswith('\\'):
                    cont = True
                    prev_line = line[:-1]
                    continue
                cont = False

                # Collect any Kconfig sources
                m = re.match(r'^source\s+"?([^"]+)', line)
                if m:
                    state = 'SOURCE'
                    self._read_kconfig(m.group(1))
                    continue

                # Block boundary
                if re.match(r'^(comment|choice|endchoice|if|endif|' +
                            r'endmenu)\b', line):
                    state = 'NONE'
                    continue

                # Config found
                m = re.match(r'^\s*(menu)?config\s+(\S+)\s*$', line)
                if m:
                    state = 'CONFIG'
                    config = m.group(2)
                    # Add the Kconfig file that references this config option
                    if config not in self.configs:
                        self.configs[config] = {
                            'kconfig': [kconfig],
                            'help': [],
                        }
                    else:
                        self.configs[config]['kconfig'].append(kconfig)
                    continue

                # Config help found
                if state == 'CONFIG' and re.match(r'^\s*(---)?help(---)?\s*$',
                                                  line):
                    state = 'CONFIG_HELP'
                    continue

                # Collect (non-empty) config help lines
                if state == 'CONFIG_HELP' and line.strip():
                    self.configs[config]['help'].append(line.strip())
                    continue

    def save(self, filename):
        with open(filename, 'w') as fh:
            json.dump(self.configs, fh)


if __name__ == '__main__':
    arch = 'amd64'
    config = None
    if len(sys.argv) > 1:
        arch = sys.argv[1]
    if len(sys.argv) > 2:
        config = re.sub(r'^CONFIG_', '', sys.argv[2])

    kconfig = Kconfig('.', 'Kconfig', arch)
    if config:
        data = kconfig.configs.get(config, {'help': []})
        print(config)
        print('\n'.join(['  ' + h for h in data['help']]))

    else:
        for config, data in kconfig.configs.items():
            print()
            print(config)
            print('\n'.join(['  ' + h for h in data['help']]))
