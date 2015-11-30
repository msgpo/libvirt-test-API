#! /usr/bin/env python
# Install a linux domain from network

import os
import sys
import re
import time
import commands
import shutil
import urllib


import libvirt
from libvirt import libvirtError

from src import sharedmod
from src import env_parser
from utils import utils

required_params = ('guestname', 'guestos', 'guestarch', 'netmethod')
optional_params = {'memory': 1048576,
                   'vcpu': 2,
                   'disksize' : 10,
                   'imageformat' : 'qcow2',
                   'hddriver' : 'virtio',
                   'nicdriver': 'virtio',
                   'nettype': 'network',
                   'netsource': 'default',
                   'type' : 'define',
                   'xml': 'xmls/kvm_linux_guest_install_net.xml',
                   'guestmachine': 'pc',
                   'graphic':'spice',
                   'hostip':'127.0.0.1',
                   'user':'root',
                   'password':'redhat',
                   'disksymbol':'sdb',
                   'diskpath':"/var/lib/libvirt/images"
                  }

VIRSH_QUIET_LIST = "virsh --quiet list --all|awk '{print $2}'|grep \"^%s$\""
VM_STAT = "virsh --quiet list --all| grep \"\\b%s\\b\"|grep off"
VM_DESTROY = "virsh destroy %s"
VM_UNDEFINE = "virsh undefine %s"

BOOT_DIR = "/var/lib/libvirt/boot"
VMLINUZ = os.path.join(BOOT_DIR, 'vmlinuz')
INITRD = os.path.join(BOOT_DIR, 'initrd.img')
HOME_PATH = os.getcwd()

def get_remote_hypervisor_uri(hostip, user, password):
    (ret1, out1) = utils.remote_exec_pexpect(hostip, user, password, 'lsmod|grep kvm')
    (ret2, out2) = utils.remote_exec_pexpect(hostip, user, password, 'ls /proc|grep xen')
    if strip(out1) != "":
        return "qemu+ssh://%s/system" %hostip
    elif strip == "xen":
        return "xen+ssh://%s" %hostip
    else:
        return "No hypervisor running" 

def prepare_boot_guest(domobj, xmlstr, guestname, logger, installtype, installmethod):
    """After guest installa/kvm_linux_guest_install_net.xml
on is over, undefine the guest with
       bootting off cdrom, to define the guest to boot off harddisk.
    """

    xmlstr = re.sub("<kernel>.*</kernel>\n", "", xmlstr)
    xmlstr = re.sub("<initrd>.*</initrd>\n", "", xmlstr)
    xmlstr = re.sub("<cmdline>.*</cmdline>\n", "", xmlstr)
 
    if installmethod == "nfs":
        xmlstr = re.sub("<interface type='direct'>", \
                        "<interface type='network'>", \
                        xmlstr)
        xmlstr = re.sub("<source dev=.* mode='bridge'/>", \
                        "<source network='default'/>", \
                        xmlstr)
        xmlstr = re.sub("\n.*<target dev='macvtap0'/>", "", xmlstr)
        xmlstr = re.sub("<alias name=.*>\n", "", xmlstr)
   

    if installtype != 'create':
        domobj.undefine()
        logger.info("undefine %s : \n" % guestname)

    try:
        conn = domobj._conn
        domobj = conn.defineXML(xmlstr)
    except libvirtError, e:
        logger.error("API error message: %s, error code is %s" \
                     % (e.message, e.get_error_code()))
        logger.error("fail to define domain %s" % guestname)
        return 1

    logger.info("define guest %s " % guestname)
    logger.debug("the xml description of guest booting off harddisk is %s" %
                 xmlstr)

    logger.info('boot guest up ...')

    try:
        domobj.create()
    except libvirtError, e:
        logger.error("API error message: %s, error code is %s" \
                     % (e.message, e.get_error_code()))
        logger.error("fail to start domain %s" % guestname)
        return 1

    return 0

def check_domain_state(conn, guestname, logger):
    """ if a guest with the same name exists, remove it """
    running_guests = []
    ids = conn.listDomainsID()
    for id in ids:
        obj = conn.lookupByID(id)
        running_guests.append(obj.name())

    if guestname in running_guests:
        logger.info("A guest with the same name %s is running!" % guestname)
        logger.info("destroy it...")
        domobj = conn.lookupByName(guestname)
        domobj.destroy()

    defined_guests = conn.listDefinedDomains()

    if guestname in defined_guests:
        logger.info("undefine the guest with the same name %s" % guestname)
        domobj = conn.lookupByName(guestname)
        domobj.undefine()

def install_linux_net_remote(params):
    """install a new virtual machine"""
    # Initiate and check parameters
    logger = params['logger']

    guestname = params.get('guestname')
    guestos = params.get('guestos')
    guestarch = params.get('guestarch')
    nettype = params.get('nettype')
    netsource = params.get('netsource')
    xmlstr = params['xml']
    installmethod = params['netmethod']
    hostip = params.get('hostip','127.0.0.1')
    user = params.get('user','root')
    password = params.get('password','redhat')
    graphic = params.get('graphic','spice')
   
    diskpath = params.get('diskpath',"/var/lib/libvirt/images")

    logger.info("the name of guest is %s" % guestname)
    logger.info("the installation method is %s" % installmethod)
    #Remote or local installation 
    if hostip == "127.0.0.1":
        conn = sharedmod.libvirtobj['conn']
    else:
        remoteuri = utils.get_uri(hostip)
        if utils.do_ping(hostip, 50):
            if sharedmod.libvirtobj.has_key(remoteuri):
                conn = sharedmod.libvirtobj[remoteuri]
            else:
                #sshauth.sshauth(hostip, password, logger)
                conn = utils.get_conn(remoteuri, user, password)
                sharedmod.libvirtobj[remoteuri] = conn
        else:
            logger.info("Please check the interface, ping host fails~")
            logger.debug("Internet fail")
            return 1
    
    check_domain_state(conn, guestname, logger)
    macaddr = utils.get_rand_mac()

    logger.info("the macaddress is %s" % macaddr)
    
    # Seize the path and command
    # Beware of that the generation will replace the DISKPATH automatically
    xmlstr = xmlstr.replace(diskpath, "DISKPATH")
    diskpath = diskpath + "/" + guestname
    hddriver = params.get('hddriver', 'virtio')
    if hddriver != 'lun' and hddriver != 'scsilun':
        logger.info("disk image is %s" % diskpath)
        seeksize = params.get('disksize', 6)
        imageformat = params.get('imageformat', 'qcow2')
        logger.info("create disk image with size %sG, format %s" % (seeksize, imageformat))
        disk_create = "qemu-img create -f %s %s %sG" % \
                        (imageformat, diskpath, seeksize)
        logger.debug("the command line of creating disk images is '%s'" % \
                       disk_create)

        if hostip == "127.0.0.1":
            (status, message) = commands.getstatusoutput(disk_create)
            os.chown(diskpath, 107, 107) 
        else:
            (status, message) = utils.remote_exec_pexpect(hostip,user,password,disk_create)
            chowncommand = "chown 107:107" + diskpath

    #Authorize the file and directory
        if status != 0:
            logger.debug(message)
            return 1
        
        logger.info("creating disk images file is successful.")


    if hddriver == 'virtio':
        xmlstr = xmlstr.replace('DEV', 'vda')
    elif hddriver == 'ide':
        xmlstr = xmlstr.replace('DEV', 'hda')
    elif hddriver == 'scsi':
        xmlstr = xmlstr.replace('DEV', 'sda')
    elif hddriver == 'scsilun':
        xmlstr = xmlstr.replace("'scsilun'","'scsi'")
        xmlstr = xmlstr.replace('DEV','vda')
        xmlstr = xmlstr.replace('"file"','"block"')
        xmlstr = xmlstr.replace('"disk"','"lun"')
        xmlstr = xmlstr.replace("file='DISKPATH'","dev='/dev/SDX'")
        disksymbol = params.get('disksymbol','sdb')
        xmlstr = xmlstr.replace('SDX',disksymbol)
    elif hddriver == 'lun':
        xmlstr = xmlstr.replace("'lun'","'virtio'")
        xmlstr = xmlstr.replace('DEV','vda')
        xmlstr = xmlstr.replace('"file"','"block"')
        xmlstr = xmlstr.replace('"disk"','"lun"')
        xmlstr = xmlstr.replace("file='DISKPATH'","dev='/dev/SDX'")
        disksymbol = params.get('disksymbol','sdb')
        xmlstr = xmlstr.replace('SDX',disksymbol)
    elif hddriver == 'sata':
        xmlstr = xmlstr.replace('DEV','sda')

    logger.info("get system environment information")
    envfile = os.path.join(HOME_PATH, 'global.cfg')
    logger.info("the environment file is %s" % envfile)
    
    #Setting grahoic work
    if graphic == "vnc":
        xmlstr = xmlstr.replace('spice','vnc')
    elif graphic == "spice":
        xmlstr = xmlstr.replace('spice','spice')
    else:
        logger.info("graphic type unsupported")
        return 1

    envparser = env_parser.Envparser(envfile)

    # Get http, ftp or nfs url based on guest os, arch
    # and installation method from global.cfg

    os_arch = guestos + "_" + guestarch

    if installmethod == 'http':
        ks = envparser.get_value("guest", os_arch + "_http_ks")
        nettype = "network"
        netsource = "default"
    elif installmethod == 'ftp':
        ks = envparser.get_value("guest", os_arch + "_ftp_ks")
        nettype = "network"
        netsource = "default"
    elif installmethod == "nfs":
        ks = envparser.get_value("guest", os_arch + "_nfs_ks")
        nettype = "bridge"
        netsource = "br0"

    xmlstr = xmlstr.replace('KS', ks)

    ostree = envparser.get_value("guest", os_arch)
    logger.debug('install source:\n    %s' % ostree)
    logger.debug('kisckstart file:\n    %s' % ks)

    if (ostree == 'http://'):
        logger.error("no os tree defined in %s for %s" % (envfile, os_arch))
        return 1

    logger.info('prepare installation...')

    vmlinuzpath = os.path.join(ostree, 'isolinux/vmlinuz')
    initrdpath = os.path.join(ostree, 'isolinux/initrd.img')

    logger.debug("the url of vmlinuz file is %s" % vmlinuzpath)
    logger.debug("the url of initrd file is %s" % initrdpath)

    if hostip == "127.0.0.1":
        urllib.urlretrieve(vmlinuzpath, VMLINUZ)
        urllib.urlretrieve(initrdpath, INITRD)
    else:
        urlcommand = """echo "import urllib;urllib.urlretrieve('%s','%s');\
                     urllib.urlretrieve('%s','%s')">/var/lib/libvirt/temp.py """ \
                     %(vmlinuzpath, VMLINUZ, initrdpath, INITRD)
        utils.remote_exec_pexpect(hostip, user, password, urlcommand+
                                 ";python /var/lib/libvirt/temp.py;\
                                 rm -rf /var/lib/libvirt/temp.py")

    logger.debug("vmlinuz and initrd.img are located in %s" % BOOT_DIR)

    xmlstr = xmlstr.replace('KERNEL', VMLINUZ)
    xmlstr = xmlstr.replace('INITRD', INITRD)
    xmlstr = xmlstr.replace('MACADDR', macaddr)
    xmlstr = xmlstr.replace('DISKPATH', diskpath)
    xmlstr = xmlstr.replace('NETTYPE', nettype)
    xmlstr = xmlstr.replace('NETSOURCE', netsource)

    logger.debug('dump installation guest xml:\n%s' % xmlstr)

    installtype = params.get('type', 'define')
    if installtype == 'define':
        logger.info('define guest from xml description')
        try:
            domobj = conn.defineXML(xmlstr)
        except libvirtError, e:
            logger.error("API error message: %s, error code is %s" \
                         % (e.message, e.get_error_code()))
            logger.error("fail to define domain %s" % guestname)
            return 1

        logger.info('start installation guest ...')

        try:
            domobj.create()
        except libvirtError, e:
            logger.error("API error message: %s, error code is %s" \
                         % (e.message, e.get_error_code()))
            logger.error("fail to start domain %s" % guestname)
            return 1
    elif installtype == 'create':
        logger.info('create guest from xml description')
        try:
            domobj = conn.createXML(xmlstr, 0)
        except libvirtError, e:
            logger.error("API error message: %s, error code is %s" \
                         % (e.message, e.get_error_code()))
            logger.error("fail to define domain %s" % guestname)
            return 1

    if 'rhel3u9' in guestos:
        interval = 0
        logger.info("waiting 1000 seconds for the installation to complete...")
        while(interval < 1000):
            logger.info('%s seconds passed away...' % interval)
            time.sleep(10)
            interval += 10

        domobj.destroy()
        ret =  prepare_boot_guest(domobj, xmlstr, guestname, logger, installtype, \
                                  installmethod)

        if ret:
            logger.info("booting guest vm off harddisk failed")
            return 1
        else:
            logger.info("geust is booting up")
    else:
        interval = 0
        while(interval < 3600):
            time.sleep(10)
            if installtype == None or installtype == 'define':
                state = domobj.info()[0]
                if(state == libvirt.VIR_DOMAIN_SHUTOFF):
                    logger.info("guest installaton of define type is complete")
                    logger.info("boot guest vm off harddisk")
                    ret  = prepare_boot_guest(domobj, xmlstr, guestname, logger, \
                                              installtype, installmethod)
                    if ret:
                        logger.info("booting guest vm off harddisk failed")
                        return 1
                    break
                else:
                    interval += 10
                    logger.info('%s seconds passed away...' % interval)
            elif installtype == 'create':
                guest_names = []
                ids = conn.listDomainsID()
                for id in ids:
                    obj = conn.lookupByID(id)
                    guest_names.append(obj.name())

                if guestname not in guest_names:
                    logger.info("guest installation of create type is complete")
                    logger.info("define the vm and boot it up")
                    ret = prepare_boot_guest(domobj, xmlstr, guestname, logger, \
                                             installtype, installmethod)
                    if ret:
                        logger.info("booting guest vm off harddisk failed")
                        return 1
                    break
                else:
                    interval += 10
                    logger.info('%s seconds passed away...' % interval)

        if interval == 3600:
            logger.info("guest installation timeout 3600s")
            return 1
        else:
            logger.info("guest is booting up")

    logger.info("get the mac address of vm %s" % guestname)
    if hostip == "127.0.0.1":
        mac = utils.get_dom_mac_addr(guestname)
    else:
        mac = utils.get_remote_dom_mac_addr(hostip, user, password, guestname)
    logger.info("the mac address of vm %s is %s" % (guestname, mac))

    timeout = 300
    while timeout:
        time.sleep(10)
        timeout -= 10
        if hostip == "127.0.0.1":
            ip = utils.mac_to_ip(mac, 180)
        else:
            ip = utils.remote_mac_to_ip(hostip, user, password, mac, 180)

        if not ip:
            logger.info(str(timeout) + "s left")
        else:
            logger.info("vm %s power on successfully" % guestname)
            logger.info("the ip address of vm %s is %s" % (guestname, ip))
            break

        if timeout == 0:
            logger.info("fail to power on vm %s" % guestname)
            return 1

    return 0

def install_linux_net_remote_clean(params):
    """ clean testing environment """
    logger = params['logger']
    guestname = params.get('guestname')

    diskpath = params.get('diskpath', "/var/lib/libvirt/images") + "/" + guestname
 
    (status, output) = commands.getstatusoutput(VIRSH_QUIET_LIST % guestname)
    if not status:
        logger.info("remove guest %s, and its disk image file" % guestname)
        (status, output) = commands.getstatusoutput(VM_STAT % guestname)
        if status:
            (status, output) = commands.getstatusoutput(VM_DESTROY % guestname)
            if status:
                logger.error("failed to destroy guest %s" % guestname)
                logger.error("%s" % output)
            else:
                (status, output) = commands.getstatusoutput(VM_UNDEFINE % guestname)
                if status:
                    logger.error("failed to undefine guest %s" % guestname)
                    logger.error("%s" % output)
        else:
            (status, output) = commands.getstatusoutput(VM_UNDEFINE % guestname)
            if status:
                logger.error("failed to undefine guest %s" % guestname)
                logger.error("%s" % output)

    if os.path.exists(diskpath):
        os.remove(diskpath)

    if os.path.exists(VMLINUZ):
        os.remove(VMLINUZ)
    if os.path.exists(INITRD):
        os.remove(INITRD)
    hostip = params.get('hostip','127.0.0.1')
    if hostip != "127.0.0.1":
        remoteuri = utils.get_uri(hostip)
        if sharedmod.libvirtobj.has_key(remoteuri):
            try:
                 sharedmod.libvirtobj[remoteuri].close()
                 time.sleep(10)
            except libvirtError,e:
                logger.error("API error message: %s, error code is %s" \
                         % (e.message, e.get_error_code()))
                logger.error("fail to close the connection: %s" % remoteuri)
            sharedmod.libvirtobj.pop(remoteuri)
            logger.info("Close the connect %s" % remoteuri)