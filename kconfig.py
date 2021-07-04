#!/usr/bin/env python3
#
# A simple kernel Kconfig parser
#
# Loosly based on streamline_config.pl and checkkconfigsymbols.py from the
# linux kernel source at https://git.kernel.org/. Kudos to the kernel
# developers.
#

import logging
import os
import re

# Regex expressions
SOURCE =  r'^\s*source\s+"([^"]+)"\s*($|#)'

# Regex objects
RE_SOURCE = re.compile(SOURCE)

# Mapping between Debian package and kernel source architecture names
SRCARCH = {
    # Debian arches
    'amd64': 'x86',
    'arm64': 'arm64',
    'armhf': 'arm',
    'i386': 'x86',
    's390x': 's390',
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
    def __init__(self, ksource, kconfig, arch, log_level=logging.INFO,
                 test=False):
        self.ksource = ksource
        self.kconfig = kconfig
        self.arch = arch
        self.test = test
        self.symbols = {}

        # Setup the logger
        logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=log_level)
        self._log = logging.getLogger(__name__)

        # The list of all makefiles
        self._makefiles = []

        # 'if' conditions
        self._if = []

        # 'menu' conditions
        self._menu = []

        # 'choice' conditions
        self._choice = []

        # Parse the Kconfig tree
        self._kconfigs = {}
        self._parse_kconfig(self.kconfig)

    def _log_line(self, tokens, line, warning=False):
        token = '[{}]'.format(':'.join([t.lower() for t in tokens]))
        if warning:
            self._log.warning('%18s : %s', token, line)
        else:
            self._log.debug('%20s : %s', token, line)

    def _find_makefiles(self):
        """
        Find all Makefiles and Kbuild files
        """
        self._log.debug('Find Makefiles an Kbuild files')

        result = []
        for path, _dirs, files in os.walk(self.ksource):
            rel_path = os.path.relpath(path, self.ksource)
            for f in files:
                if f in ('Makefile', 'Kbuild'):
                    result.append(os.path.join(rel_path, f))
        return result

    def _test_regex(self, line):
        """
        Test the different regex expressions
        """
        if not line:
            return

        m = RE_SOURCE.match(line)
        if m:
            print('{} | {} | {}'.format('RE_SOURCE', line, m.group(1)))

    def _parse_kconfig(self, kconfig):
        """
        Parse the provided kconfig file and recursively traverse all included
        kconfig files as well.
        """
        # Prevent reading the same Kconfig multiple times
        if kconfig in self._kconfigs:
            return
        self._kconfigs[kconfig] = 1

        # Assemble the full kconfig file path and replace environment variables
        source = os.path.join(self.ksource, kconfig)
        source = source.replace('$(SRCARCH)', SRCARCH.get(self.arch, self.arch))
        source = source.replace('$SRCARCH', SRCARCH.get(self.arch, self.arch))

        with open(source) as fh:
            token = 'NONE'
            option = 'NONE'
            name = None
            help_indent = 0

            for line in read_line(fh):
                if self.test:
                    self._test_regex(line)

                # Determine the line indentation
                line_indent = len(re.match(r'^(\s*)', line).group(1))

                # -------------------------------------------------------------
                # Collect config help lines
                if token == 'CONFIG' and option == 'HELP':
                    if not help_indent and line_indent:
                        # Indentation of first line of help text
                        help_indent = line_indent
                    if help_indent:
                        if line and line_indent < help_indent:
                            # End of help, remove trailing empty lines
                            while not self.symbols[name]['help'][-1]:
                                del self.symbols[name]['help'][-1]
                            option = 'NONE'
                        else:
                            self._log_line([token, 'help_text'], line)
                            self.symbols[name]['help'].append(line[help_indent:])
                            continue

                # -------------------------------------------------------------
                # Ignore choice help lines
                if token == 'CHOICE' and option == 'HELP':
                    if not help_indent and line_indent:
                        # Indentation of first line of help text
                        help_indent = line_indent
                    if help_indent:
                        if line and line_indent < help_indent:
                            # End of help
                            option = 'NONE'
                        else:
                            self._log_line([token, 'help_text'], line)
                            continue

                # -------------------------------------------------------------
                # Source included Kconfig file
                m = RE_SOURCE.match(line)
                if m:
                    token = 'SOURCE'
                    option = 'NONE'
                    self._log_line([token], line)
                    self._parse_kconfig(m.group(1))
                    continue

                # -------------------------------------------------------------
                # Ignore comments
                if re.match(r'^\s*#', line):
                    self._log_line(['#'], line)
                    continue

                # -------------------------------------------------------------
                # Variable assignment
                if re.match(r'^\S+\s+(:)?=', line):
                    self._log_line(['variable'], line)
                    continue

                # -------------------------------------------------------------
                # Macro definition
                if re.match(r'^\$\(.+\)', line):
                    self._log_line(['macro'], line)
                    continue

                # -------------------------------------------------------------
                # 'if' statement
                m = re.match(r'^if\s+(.*)$', line)
                if m:
                    self._log_line(['if'], line)
                    self._if.append(m.group(1))
                    continue

                # 'endif' statement
                if re.match(r'^endif\b', line):
                    self._log_line(['endif'], line)
                    self._if.pop()
                    continue

                # -------------------------------------------------------------
                # 'comment' found
                if ((re.match(r'^\s*comment\s+"[^"]+"', line) or
                     re.match(r"^comment\s+'[^']+'", line))):
                    token = 'COMMENT'
                    self._log_line([token], line)
                    continue

                if token == 'COMMENT':
                    # Comment 'depends on' found
                    if re.match(r'^\s*depends\s+on\s+', line):
                        option = 'DEPENDS_ON'
                        self._log_line([token, option], line)
                        continue

                # -------------------------------------------------------------
                # 'menu' found
                m = re.match(r'^\s*(main)?menu\s+"([^"]+)"', line)
                if m:
                    token = 'MENU'
                    self._log_line([token], line)
                    self._menu.append({
                        'menu': m.group(2),
                        'depends_on': [],
                        'visible_if': [],
                    })
                    continue

                # 'endmenu' found
                if re.match(r'^endmenu\b', line):
                    token = 'ENDMENU'
                    self._log_line([token], line)
                    self._menu.pop()
                    continue

                if token == 'MENU':
                    # Menu 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        option = 'DEPENDS_ON'
                        self._log_line([token, option], line)
                        self._menu[-1]['depends_on'].append(m.group(1))
                        continue

                    # Menu 'visible if' found
                    m = re.match(r'^\s*visible\s+if\s+(.*)$', line)
                    if m:
                        option = 'VISIBLE_IF'
                        self._log_line([token, option], line)
                        self._menu[-1]['visible_if'].append(m.group(1))
                        continue

                # -------------------------------------------------------------
                # 'choice' found
                if re.match(r'^choice\b', line):
                    token = 'CHOICE'
                    self._log_line([token], line)
                    self._choice.append({
                        'prompt': '',
                        'depends_on': [],
                        'default': [],
                        'type': [],
                    })
                    continue

                # 'endchoice' found
                if re.match(r'^endchoice\b', line):
                    token = 'ENDCHOICE'
                    self._log_line([token], line)
                    self._choice.pop()
                    continue

                if token == 'CHOICE':
                    # Choice 'prompt' found
                    m = re.match(r'^\s+prompt\s+"([^"]+)"', line)
                    if m:
                        option = 'PROMPT'
                        self._log_line([token, option], line)
                        self._choice[-1]['prompt'] = m.group(1)
                        continue

                    # Choice 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        option = 'DEPENDS_ON'
                        self._log_line([token, option], line)
                        self._choice[-1]['depends_on'].append(m.group(1))
                        continue

                    # Choice 'default' found
                    m = re.match(r'^\s*default\s+(.*)$', line)
                    if m:
                        option = 'DEFAULT'
                        self._log_line([token, option], line)
                        self._choice[-1]['default'].append(m.group(1))
                        continue

                    # Choice 'help' found
                    if re.match(r'^\s*(---)?help(---)?\s*$', line):
                        option = 'HELP'
                        self._log_line([token, option], line)
                        help_indent = 0
                        continue

                    # Choice 'bool', 'string', 'int', 'tristate' or 'hex' found
                    m = re.match(r'^\s*(bool|string|int|tristate|hex)(|\s+(.*))$',
                                 line)
                    if m:
                        option = 'TYPE'
                        self._log_line([token, option], line)
                        self._choice[-1]['type'].append({
                            m.group(1): m.group(3),
                        })
                        continue

                # -------------------------------------------------------------
                # Config found
                m = re.match(r'^\s*(menu)?config\s+([0-9a-zA-Z_]+)', line)
                if m:
                    token = 'CONFIG'
                    self._log_line([token], line)
                    name = m.group(2)
                    # Initialize the config data hash
                    if name not in self.symbols:
                        self.symbols[name] = {
                            'kconfig': [],
                            'help': [],
                            'depends_on': [],
                            'select': [],
                            'type': [],
                            'default': [],
                            'range': [],
                            'option': [],
                            'imply': [],
                            'prompt': [],
                            'if': self._if.copy(),
                            'menu': self._menu.copy(),
                            'choice': self._choice.copy(),
                        }
                    # Add the Kconfig file that references this option
                    self.symbols[name]['kconfig'].append(kconfig)
                    continue

                if token == 'CONFIG':
                    # Config 'help' found
                    if re.match(r'^\s*(---)?help(---)?\s*$', line):
                        option = 'HELP'
                        self._log_line([token, option], line)
                        help_indent = 0
                        continue

                    # Config 'depends on' found
                    m = re.match(r'^\s*depends\s+on\s+(.*)$', line)
                    if m:
                        option = 'DEPENDS_ON'
                        self._log_line([token, option], line)
                        self.symbols[name]['depends_on'].append(m.group(1))
                        continue

                    # Config 'select' found
                    m = re.match(r'^\s*select\s+(.*)$', line)
                    if m:
                        option = 'SELECT'
                        self._log_line([token, option], line)
                        self.symbols[name]['select'].append(m.group(1))
                        continue

                    # Config 'bool', 'string', 'int', 'tristate' or 'hex' found
                    m = re.match(r'^\s*(bool|string|int|tristate|hex)(|\s+(.*))$',
                                 line)
                    if m:
                        option = 'TYPE'
                        self._log_line([token, option], line)
                        self.symbols[name]['type'].append({
                            m.group(1): m.group(3),
                        })
                        continue

                    # Config 'def_bool', 'def_tristate' or 'default' found
                    m = re.match(r'^\s*(def_bool|def_tristate|default)\s+(.*)$',
                                 line)
                    if m:
                        option = 'DEFAULT'
                        self._log_line([token, option], line)
                        self.symbols[name]['default'].append({
                            m.group(1): m.group(2),
                        })
                        continue

                    # Config 'range' found
                    m = re.match(r'^\s*range\s+(.*)$', line)
                    if m:
                        option = 'RANGE'
                        self._log_line([token, option], line)
                        self.symbols[name]['range'].append(m.group(1))
                        continue

                    # Config 'option' found
                    m = re.match(r'^\s*option\s+(.*)$', line)
                    if m:
                        option = 'OPTION'
                        self._log_line([token, option], line)
                        self.symbols[name]['option'].append(m.group(1))
                        continue

                    # Config 'imply' found
                    m = re.match(r'^\s*imply\s+(.*)$', line)
                    if m:
                        option = 'IMPLY'
                        self._log_line([token, option], line)
                        self.symbols[name]['imply'].append(m.group(1))
                        continue

                    # Config 'prompt' found
                    m = re.match(r'^\s*prompt\s+"([^"]+)"', line)
                    if m:
                        option = 'PROMPT'
                        self._log_line([token, option], line)
                        self.symbols[name]['prompt'].append(m.group(1))
                        continue

                # -------------------------------------------------------------
                # Unprocessed lines

                if line:
                    self._log_line([token, 'ignored'], line, warning=True)

    def module_to_config(self, module):
        """
        Return the config option that enables the provided kernel module
        """
        if not self._makefiles:
            self._makefiles = self._find_makefiles()

        for f in self._makefiles:
            with open(os.path.join(self.ksource, f)) as fh:
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

    def config_to_module(self, config):
        """
        Return the kernel module that is enabled by the provided config option
        """
        if not self._makefiles:
            self._makefiles = self._find_makefiles()

        for f in self._makefiles:
            with open(os.path.join(self.ksource, f)) as fh:
                for line in read_line(fh):
                    m = re.match(r'obj-\$\(CONFIG_{}\)\s*[+:]?=\s*([^\s]+)'.format(config),
                                 line)
                    if m and m.group(1).endswith('.o'):
                        return os.path.join(os.path.dirname(f),
                                            m.group(1).replace('.o', '.ko'))
        return None
