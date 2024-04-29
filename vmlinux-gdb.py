import gdb, os.path, string, re, tempfile
# vim:et:ts=4:sw=4:autoindent

""" This file adds various commands to gdb for use with debugging the linux
kernel.  Some of the routines can help automate loading symbols of dynamic
kernel modules.  

This file should be placed in the top of the kernel build directory. gdb will
load this automatically when kernel debug commences.  This file depends on
using gdb 7.1 or higher configured --with-python.

"""

long_type = gdb.lookup_type('unsigned long')

# These functions are counterparts to the Linux kernel macros
def offset(gdb_type, member):
    '''Return the offset of a member within a structure.'''
    ptr = gdb.Value(0).cast(gdb_type.pointer())
    return ptr[member].address.cast(long_type)

def list_entry(list_head_ptr, gdb_type, off):
    '''Return the base element that holds a list_head struct.'''
    base = list_head_ptr.cast(long_type) - off
    return base.cast(gdb_type.pointer())

def listhead_iter(list_head, typename, member):
    '''Iterate over elements of a list_head.  The first argument should
be a *reference* to the main list_head.'''
    type = gdb.lookup_type(typename)
    off = offset(type, member)
    pos = list_head['next']
    while pos != list_head.address:
        yield list_entry(pos, type, off)
        pos = pos['next']

def run(cmd):
    """Run a gdb command and get the output."""
    tfile = tempfile.mkstemp()[1]
    gdb.execute("set logging file %s" % tfile)
    gdb.execute("set logging on")
    gdb.execute(cmd)
    gdb.execute("set logging off")
    f = open(tfile)
    lines = f.readlines()
    f.close()
    return lines

def findBuildPath(m):
    """Find the .ko file in the kernel build tree."""
    try:
        paths = os.popen("find . ! -path './lib/modules/*' -name %s.ko" % m)
        filename = paths.readline().strip() # use the first one we find
        paths.close()
        return filename
    except:
        return ''

class LsmodCmd(gdb.Command):
    '''Load symbols for all currently-running kernel modules.'''

    def __init__(self):
        super(LsmodCmd, self).__init__('lsmod',
                                            gdb.COMMAND_FILES,
                                            gdb.COMPLETE_FILENAME)

    def invoke(self, filename, from_tty):
        try:
            frame = gdb.selected_frame()
            modules = frame.read_var("modules")
        except:
            print 'A running kernel must be attached in order to get section information'
            return
        for mod in listhead_iter(modules, 'struct module', 'list'):
            print mod['name'].string()
        return

class AddAllModsCmdFast(gdb.Command):
    '''Load symbols for all currently-running kernel modules over the network interface.'''

    def __init__(self):
        super(AddAllModsCmdFast, self).__init__('add-kernel-modules-network',
                                            gdb.COMMAND_FILES,
                                            gdb.COMPLETE_NONE)

    def invoke(self, target, from_tty):
        self.dont_repeat()
        if not target:
            print 'This command gets the module data  from the target over an ssh connection.'
            print 'EXAMPLE add-kernel-modules-network root@10.1.2.3'
            return
        gdb.execute("monitor go")
        mods = os.popen("ssh %s 'find /sys/module/ -path \'*/sections/*\' -type f -exec grep -H 0x {} \\;'" % target)
        expr = re.compile(r'/sys/module/(\w+)/sections/([.\w]+):(\w+)')
        database = {}
        for line in mods.readlines():
            try:
                record = expr.match(line).groups()
            except:
                continue
            module = record[0]
            section = record[1]
            address = record[2]

            if not database.has_key(module):
                database[module] = {}
            database[module][section] = address

        mods.close()

        for name, sects in database.iteritems():
            filename = findBuildPath(name)
            if not filename or not sects.has_key('.text'):
                continue
            cmd = 'add-symbol-file %s %s' % (filename, sects['.text'])
            for sec, addr in sects.iteritems():
                cmd += ' -s %s %s' % (sec, addr)
            print cmd
            try:
                gdb.execute(cmd)
            except:
                print "Error: unable to load symbols for %s.\n" % filename
        del database
        gdb.execute("monitor halt")

class AddAllModsCmd(gdb.Command):
    '''Load symbols for all currently-running kernel modules.'''

    def __init__(self):
        super(AddAllModsCmd, self).__init__('add-kernel-modules',
                                            gdb.COMMAND_FILES,
                                            gdb.COMPLETE_FILENAME)

    def invoke(self, filename, from_tty):
        self.dont_repeat()
        try:
            frame = gdb.selected_frame()
            modules = frame.read_var("modules")
        except:
            print 'A running kernel must be attached in order to get section information'
            return
        for mod in listhead_iter(modules, 'struct module', 'list'):
            # Now, get the section attributes
            filename = findBuildPath(mod['name'].string())
            if not os.path.isfile(filename):
                print 'Could not find module file for %s' % mod['name'].string()
                continue
            sect = mod['sect_attrs']
            num_sect = int(str(sect['nsections']))
            attrs = [ sect['attrs'][i] for i in range(num_sect) ]
            adict = dict([ (a['name'].string(), str(a['address']))
                           for a in attrs ])
            if not adict.has_key('.text'):
                continue
            cmd = 'add-symbol-file %s %s' % (filename, adict['.text'])
            for sec in ('.bss', '.data', '.init.text'):
                if sec in adict:
                    cmd += ' -s %s %s' % (sec, adict[sec])
            try:
                gdb.execute(cmd)
            except:
                print "Error: unable to load symbols for %s.\n" % filename

class AddKernModCmd(gdb.Command):
    '''Load symbols for a currently-running kernel module.'''

    def __init__(self):
        super(AddKernModCmd, self).__init__('modprobe',
                                            gdb.COMMAND_FILES,
                                            gdb.COMPLETE_FILENAME)

    def invoke(self, modname, from_tty):
        self.dont_repeat()
        if modname == '':
            print 'USAGE: modprobe module'
            return
        filename = findBuildPath(modname)
        if not os.path.isfile(filename):
            print 'Could not find module file for %s' % modname
            return
        try:
            frame = gdb.selected_frame()
            modules = frame.read_var("modules")
        except:
            print 'A running kernel must be attached in order to get section information'
            return
        for mod in listhead_iter(modules, 'struct module', 'list'):
            if mod['name'].string() == modname:
                # Found it!  Now, get the section attributes
                sect = mod['sect_attrs']
                num_sect = int(str(sect['nsections']))
                attrs = [ sect['attrs'][i] for i in range(num_sect) ]
                adict = dict([ (a['name'].string(), str(a['address']))
                               for a in attrs ])
                cmd = 'add-symbol-file %s %s' %(filename,adict['.text'])
                for sec in ('.bss', '.data', '.init.text'):
                    if sec in adict:
                        cmd += ' -s %s %s' % (sec, adict[sec])
                try:
                    gdb.execute(cmd)
                except:
                    print "Error: unable to load symbols for %s.\n" % filename
                return
        print 'Module %s is not currently loaded by the kernel.' % modname

class AddThisModCmd(gdb.Command):
    '''Load symbols for a currently-running kernel module.'''

    def __init__(self):
        super(AddThisModCmd, self).__init__('add-this-module',
                                            gdb.COMMAND_DATA)

    def invoke(self, name, from_tty):
        self.dont_repeat()
        try:
            frame = gdb.selected_frame()
            mod = frame.read_var("mod")
        except:
            print 'This command should be run while stopped at a breakpoint\njust after load_module() returns in kernel/module.c'
            return
        # Now, get the section attributes
        this_module = mod["name"].string()
        if not name:
            name = this_module
        if name != this_module:
            return
        filename = findBuildPath(this_module)
        if not os.path.isfile(filename):
            print 'Could not find module file for %s' % this_module
            return
        sect = mod['sect_attrs']
        num_sect = int(str(sect['nsections']))
        attrs = [ sect['attrs'][i] for i in range(num_sect) ]
        adict = dict([ (a['name'].string(), str(a['address']))
                       for a in attrs ])
        if not adict.has_key('.text'):
            return
        cmd = 'add-symbol-file %s %s' %(filename,adict['.text'])
        for sec in ('.bss', '.data', '.init.text'):
            if sec in adict:
                cmd += ' -s %s %s' % (sec, adict[sec])
        try:
            gdb.execute(cmd)
        except:
            print "Error: unable to load symbols for %s.\n" % filename

class LoadBreakCmd(gdb.Command):
    '''Set a breakpoint after a module is loaded. You can use add-this-module to load symbols for the current module.'''

    def __init__(self):
        super(LoadBreakCmd, self).__init__('module-load-break',
                                            gdb.COMMAND_BREAKPOINTS)

    def invoke(self, arg, from_tty):
        self.dont_repeat()
        # Here is a line of code that we can stop at. Once we are here, the new module is loaded, but 
        # we have not yet jumped into the code.
        paths = os.popen("grep -n -e 'if (mod->init != NULL)' kernel/*.c")
        loc = paths.readline().strip() # use the first one we find
        file, line, _ = string.split(loc, ':')
        paths.close()
        cmd = "break %s:%d" % (file, int(line))
        gdb.execute(cmd)

class GoandQuitCmd(gdb.Command):
    '''This is a convenience function that will leave the target running after quitting gdb.'''

    def __init__(self):
        super(GoandQuitCmd, self).__init__('go-and-quit',
                                            gdb.COMMAND_SUPPORT, gdb.COMPLETE_NONE)

    def invoke(self, arg, from_tty):
        gdb.execute("monitor go")
        gdb.execute("quit")

# Construct the command objects
AddKernModCmd()
AddThisModCmd()
AddAllModsCmd()
LsmodCmd()
LoadBreakCmd()
GoandQuitCmd()
AddAllModsCmdFast()

# Set a longer timeout.
gdb.execute("set remotetimeout 15")

# Determine the architecture of the vmlinux file and make sure that the stub
# agrees with it.

arch_str = run("show architecture")[0]
if arch_str.find("ia64"):
    arch = "ia64"
elif arch_str.find("x86-64"):
    arch = "x86_64"
elif arch_str.find("i386"):
    arch = "ia32"
else:
    arch = "unknown"

if arch == "ia32":
    run("monitor arch ia32")
