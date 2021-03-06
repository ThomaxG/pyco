# coding=utf-8
'''
Created on Mar 15, 2011

@author: Attilio Donà
'''
import sys
import os
import re
import time
from mako.template import Template
from mako.runtime import Context
from io import StringIO
from validate import Validator
from pkg_resources import resource_filename, resource_string, iter_entry_points #@UnresolvedImport

import pyco.log

# create logger
log = pyco.log.getLogger("device")

from configobj import ConfigObj, flatten_errors #@UnresolvedImport

expectLogfile = '/tmp/expect.log'
cfgFile = resource_filename('pyco', 'cfg/pyco.cfg')
if hasattr(pyco, 'pyco_home'):
    expectLogfile = pyco.pyco_home + "/logs/expect.log"
    if (os.path.isfile(pyco.pyco_home + "/cfg/pyco.cfg")):
        cfgFile = pyco.pyco_home + "/cfg/pyco.cfg"

# the shared configObj
configObj = None

class DeviceException(Exception):

    """This is the Device base exception class."""

    def __init__(self, device, msg=''):
        self.msg = msg
        self.device = device
        device.close()
        
    def __str__(self):
        return self.msg

class WrongDeviceUrl(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class MissingDeviceParameter(DeviceException):
    pass

class UnsupportedProtocol(DeviceException):
    pass

class ExpectException(DeviceException):
    
    # impossibile connettersi correttamente all'apparato di rete
    code = 310
    
    def __init__(self, device, msg=''):
        self.interaction_log = device.interaction_log()
        DeviceException.__init__(self, device, msg)

    def descr(self):
        return '%s: %s' % (self.device.name, self.msg)


class ConnectionClosed(ExpectException):
    '''
    Raised when EOF is read from a pexpect child. This usually means the child has exited
    '''
    pass

class ConnectionRefused(ExpectException):
    '''
    Thrown when the connection was refused by the remote endpoint
    '''
    pass        

class PermissionDenied(ExpectException):
    '''
    Thrown when the device login fails because username token or password token or both are wrong. 
    '''
    pass

class ConnectionTimedOut(ExpectException):
    '''
    Typically occurs when there is no response or when none of the expected patterns match with the device response
    '''
    pass

class CommandExecutionError(ExpectException):
    '''
    Raised when there is a error pattern match that signal a command error, for example an invalid syntax
    '''

class LoginFailed(ExpectException):
    '''
    Thrown when the login phase in not successful
    '''
    pass

class EventHandlerUndefined(Exception):
    '''
    Thrown when an event action is undefined
    '''
    def __init__(self, value):
        self.value = value
        
    def __str__(self):
        return "event handler %s not defined" % self.value
    


class ConfigFileError(Exception):
    '''
    Thrown when the pyco config file is invalid
    '''
    def __init__(self, value):
        self.value = value
        
    def __str__(self):
        return repr(self.value)

def getAccount(device):
    
    # this is the last function in the plugin group
    return False

def path(hops):
    '''
    Get the target device from the list of hops that define the path to the device.

    The target device must be the last list item.
    '''
    target = hops.pop()
    target.hops = hops
    return target

def device(url):
    '''
    Returns a Device instance builded from a url.

    the device url is compliant with the RFC syntax defined by http://tools.ietf.org/html/rfc3986
    the telnet and ssh scheme are extended with a path item defining the host specific driver to be used for connecting:

        *[protocol://][user][:password]@hostname[:port][/driver]*
    
    valid examples of device url:
        * telnet://u:p@localhost:21/linux
        * ssh://user@localhost:21/linux
        * ssh://localhost

        
    where *protocol* is one of:
      * telnet
      * ssh    

    for example:
    
      >>> h = device('ssh://jack:secret@myhost/linux')
      >>> h.username
      'jack'
      >>> h.password
      'secret'
      >>> h.name
      'myhost'
      >>> h.protocol
      'ssh'
      >>> h.driver
      driver:linux


    *protocol* is optional, it defaults to *ssh*

    *driver* is optional. If not defined it defaults to the common driver:
    
      >>> h = device('jack:secret@myhost')
      >>> h.username
      'jack'
      >>> h.password
      'secret'
      >>> h.name
      'myhost'
      >>> h.protocol
      'ssh'
      >>> h.driver
      driver:common
      
    if username or password is not defined they are set to the null value None:
      
      >>> h = device('ssh://foo@myhost')
      >>> h.username
      'foo'
      >>> h.password
      >>> h.password is None
      True
      >>> h.name
      'myhost'
      >>> h.protocol
      'ssh'
      >>> h.port
      22

      >>> h = device('telnet://:secret@myhost:2222')
      >>> h.username
      >>> h.username is None
      True
      >>> h.password
      'secret'
      >>> h.name
      'myhost'
      >>> h.protocol
      'telnet'
      >>> h.port
      2222
      
    The *driver* name has to be one of the [section] name found into the pyco configuration file :ref:`driver-configuration`.
    With this release the configured drivers are:
      
      * common
      * linux
      * ciscoios 
      
    If a driver is not configured this way an exception is thrown in the device factory function:
      
      >>> h1 = device('ssh://jack:secret@myhost/zdriver')
      Traceback (most recent call last):
        ...
      DriverNotFound: 'zdriver driver not defined'
        
    '''
    if url.startswith('telnet://') or url.startswith('ssh://'):
        pass
    else:
        url = 'ssh://' + url
    try:
        (driverName, host, user, password, protocol, port) = parseUrl(url)
    except:
        raise WrongDeviceUrl('invalid url %s' % url)
    
    if host == None:
        raise WrongDeviceUrl('hostname not defined')
    
    if user == '':
        user = None
    
    if port is None:
        if protocol == 'ssh': port = 22
        elif protocol == 'telnet' : port = 23
        
    if driverName.startswith('/'):
        driverName=driverName.lstrip('/')
    
    log.debug("[%s] info: driver [%s], cred [%s / %s], protocol [%s:%s]" % (host, driverName, user, password, protocol, port))
    
    if driverName == '':
        driverName = 'common'
    
    driver = Driver.get(driverName)
    
    obj = Device(host, driver, user, password, protocol, port)
    log.debug("[%s] builded" % host)
    return obj
    
def parseUrl(url):
    '''
    '''
    from urllib.parse import urlparse #@UnresolvedImport

    # Workaround 
    # jython (and python?) 2.5.2 urlparse does not support the ssh schema
    
    if url.startswith('ssh://'):
        purl = url.replace('ssh://', 'telnet://', 1)
        sshScheme = True
    else:
        purl = url
        sshScheme = False
        
    items = urlparse(purl)
    
    if items.scheme == '':
        items = urlparse('telnet://' + url)
    
    if sshScheme:
        return (items.path, items.hostname, items.username, items.password, 'ssh', items.port)
    else:
        return (items.path, items.hostname, items.username, items.password, items.scheme, items.port)


def defaultEventHandler(device):
    '''
    The default event handler is invoked if and only if the fsm (event,current_state) 
    fall back on the fsm default_transition 
    '''
    log.debug("[%s] in state [%s] got [%s] event" % (device.name, device.state, device.currentEvent.name))

    event_map = {
                  'eof'    : ConnectionClosed
                }
    #'timeout': ConnectionTimedOut,

    if device.currentEvent.name in event_map:
        log.info("[%s] unexpected communication error in state [%s] got [%s] event" % (device.name, device.state, device.currentEvent.name))
        exception = event_map[device.currentEvent.name](device)
        device.close()
        raise exception

def getExactStringForMatch(str):
    '''
    Used for example to escape special characters in prompt strings 
    '''
    specials = ['(\\[)', '(\\$)', '(\\.)', '(\\^)', '(\\*)', '(\\+)', '(\\?)', '(\\{)', '(\\})', '(\\])', '(\\|)', '(\\()', '(\\))']
    orSep = '|'
    pattern = orSep.join(specials)
    
    p = re.compile('(\\\\)')
    match = p.sub(r'\\\1', str)
    for spec in specials:
        p = re.compile(spec)
        match = p.sub(r'\\\1', match)
    
    return match

def discoverPromptCallback(device, tentativePrompt=None):
    '''
    The discover prompt algorithm
    '''

    if tentativePrompt is not None:
        output = tentativePrompt
    elif device.currentEvent.name == 'prompt-match':
        
        # TODO: manage a tuple of hints, for example
        # output = (device.esession.pipe.before + device.esession.pipe.after, device.esession.pipe.after)
        # because a multiline prompt actually is not correctly managed
        output = device.esession.pipe.after
        log.debug('raw output: [%s]' % output)
    elif device.currentEvent.name == 'timeout':
        output = device.esession.pipe.before
    else:
        raise Exception("discover prompt failed; unexpected event [%s]" % device.currentEvent.name)
    
    # if regular exp succeed then set the prompt
    log.debug("[%s] prompt discovery ..." % (device.name))

    # stop the default handling of the timeout event
    device.currentEvent.stopPropagation()
    
    sts = device.state

    if sts in device.prompt:
        if output.startswith('\r\n'):
            output = output.replace('\r\n', '', 1)

        log.debug('[%s] == [%s]' % (device.prompt[sts].value, output))
        if device.prompt[sts].value == output:
            device.discoveryCounter = 0
            log.debug("[%s] [%s] prompt discovered: [%s]" % (device.name, sts, device.prompt[sts].value))
            device.prompt[sts].setExactValue(device.prompt[sts].value)
            
            # TODO: save only if the cache is not aligned
            if cache_enabled():
                save_cached_prompt(device)
            
            #device.add_event_action('prompt-match', getExactStringForMatch(device.prompt[sts].value), device.fsm.current_state)
            device.add_expect_pattern('prompt-match', getExactStringForMatch(device.prompt[sts].value), sts)
            for ev in ['timeout', 'prompt-match']:
                log.debug('removing discoverPromptCallback')
                device.remove_event_handler(ev, discoverPromptCallback)
            
            # declare the discovery with the event
            device.currentEvent = Event('prompt-match')
            
            return
            
        else:
            device.remove_pattern(getExactStringForMatch(device.prompt[sts].value), sts)
            
            if device.discoveryCounter == 2:
                log.debug("[%s] [%s] unable to found the prompt, unsetting discovery. last output: [%s]" % (device.name, sts, output))
                device.discoverPrompt = False
                device.remove_event_handler('timeout', discoverPromptCallback)
                return
            else:
                device.prompt[sts].tentative = True
                if output.startswith('\r\n'):
                    output = output.replace('\r\n', '', 1)
                device.prompt[sts].value = output
                log.debug("[%s] [%s] no prompt match, retrying discovery with pointer %s" % (device.name, sts, [device.prompt[sts].value]))
                device.add_expect_pattern('prompt-match', getExactStringForMatch(device.prompt[sts].value), sts)
                device.discoveryCounter += 1
    else:
        print("OUT: " + str(output))
        rows = output.split('\r\n')
        if hasattr(device, 'promptRegexp'):
            if output.startswith('\r\n'):
                output = output.replace('\r\n', '', 1)
            tentativePrompt = output
            log.debug('promptRegexp tentativePrompt: [%s]' % tentativePrompt)
            device.remove_pattern('\r\n' + device.promptRegexp, sts)
        else:
            tentativePrompt = rows[-1]
        device.discoveryCounter = 0
        log.debug("[%s] tentativePrompt: [%s]" % (device.name, tentativePrompt))
        device.prompt[sts] = Prompt(tentativePrompt, tentative=True)
        device.add_expect_pattern('prompt-match', getExactStringForMatch(device.prompt[sts].value), sts)
        
    device.clear_buffer()
    device.send_line('')
    device.expect(lambda d: d.currentEvent.name == 'timeout' or d.currentEvent.name == 'prompt-match')


def buildPatternsList(device, driver=None):
    '''
    Setup the expect patterns and the action events from the configobj 
    '''
   
    if driver == None:
        driver = device.driver
   
    log.debug("loading driver [%s]" % driver)
    if hasattr(driver, 'parent'):
        log.debug("[%s] parent driver: [%s]" % (driver, driver.parent))
        buildPatternsList(device, Driver.get(driver.parent))
    
    if driver.name not in configObj:
        log.debug("skipping undefined [%s] section" % (driver.name))
        return
    
    for (eventKey, eventData) in list(configObj[driver.name]['events'].items()):

        action=None
        if 'action' in eventData:
            action = buildAction(eventData['action'])
           
        states = '*'  
        if 'beginState' in eventData:
            states =  eventData['beginState']
            
        endState = None
        if 'endState' in eventData:
            endState = eventData['endState']
     
        pattern = None
        if 'pattern' in eventData:
            pattern = eventData['pattern']
            
        device.add_event_action(event=eventKey, pattern=pattern, action=action, beginState=states, endState=endState)



def buildAction(actionString):
    
    if actionString.startswith(':'):
        al = actionString.split(':')
        al = al[1:-1]
    else:        
        al = actionString.split()
    
    #log.debug('[%s] splitted into [%s]' % (actionString, al))
    if len(al) > 1:
        baseAction = get_callable(al[0])
        def action(target):
            log.debug("invoking action [%s] with %s" % (baseAction.__name__, al[1:]))
            baseAction(target,*al[1:])
            
    else:
        action = get_callable(actionString)
    
    return action



def get_callable(methodName):
    '''
    From the methodName string get the first callable object looking into:
    * `$PYCO_HOME/handlers.py` python source file
    * pyco.actions internal package module
    * pyco.device internal package module
    '''
    if methodName == '' or methodName is None:
        return None

    log.debug('looking for action [%s]' % methodName)
    import pyco.actions
    if isinstance(methodName,str):
        try:
            if hasattr(pyco, 'pyco_home'):
                
                sys.path.append(pyco.pyco_home)
                
                try:
                    log.debug('looking for [%s] into actions module' % methodName)
                    import handlers #@UnresolvedImport
                    return getattr(handlers, methodName)
                except:
                    log.debug('looking for [%s] into pyco package' % methodName)
                    return getattr(pyco.actions, methodName)
            else:
                return getattr(pyco.actions, methodName)
        except:
            if methodName in globals():
                return globals()[methodName]
            else:
                raise EventHandlerUndefined(methodName)
    else:
        def composite(d):
            for m in methodName:
                get_callable(m)(d)
        return composite


def cliIsConnected(target):
    log.debug("[%s] [%s] state, [%s] event: checking if CLI is connected ..." % (target.name, target.state, target.currentEvent.name))

    if target.currentEvent.name == 'prompt-match':
        return True

    if hasattr(target, 'promptPattern'):
        log.debug('[%s] matching prompt with pattern [%s]' % (target.state, target.promptPattern))
        target.prompt[target.state] = Prompt(target.promptPattern, tentative=False)
        target.add_expect_pattern('prompt-match', target.promptPattern, target.state)
        return True
    else:

        if target.discoverPrompt:
            log.debug("[%s] starting [%s] prompt discovery" % (target.name, target.state))
            target.enable_prompt_discovery()
            
            def isTimeoutOrPromptMatch(d):
                return d.currentEvent.name == 'timeout' or d.currentEvent.name == 'prompt-match'
            
            target.expect(isTimeoutOrPromptMatch)
            
            log.debug("prompt discovery executed, cliIsConnected event: [%s]" % target.currentEvent.name)
            return target.currentEvent.name == 'prompt-match'

  
def commandError(target):
    log.error('[%s]: detected error response [%s]' % (target.name, target.esession.pipe.after))
    raise CommandExecutionError(target)


class Event:
    def __init__(self, name, propagateToFsm=True):
        self.name = name
        self.propagate = propagateToFsm
        
    def __str__(self):
        return self.name    
        
    def stopPropagation(self):
        self.propagate = False
        
    def isActive(self):
        return self.propagate
    
    def isTimeout(self):
        return self.name == 'timeout'
            
    def isPromptMatch(self):
        return self.name == 'prompt-match' or self.name.endswith('_prompt')
    
class Prompt:
    
    def __init__(self, promptValue, tentative=False):
        self.value = promptValue
        self.tentative = tentative
    
    def isTentative(self):
        return self.tentative

    def isFinal(self):
        return not self.tentative
     
    def setExactValue(self, value):
        self.value = value
        self.tentative = False


class FSMException(Exception):

    """This is the ExtFSM Exception class."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class Device:
    '''
    `Device` class models a host machine and implements the FSM behavoir.

    device object properties are:

    * `name`
        the fqdn or the host ip address

    * `username`
        the authenticated username   

    * `password`
        the authenticated password
        
    * `protocol`
        the transport protocol: actually ssh o telnet
        
    * `driver`
        the FSM driver associated with this device

    In a normal usage scenario you dont use any of the Device methods apart the :meth:`pyco.device.Device.send_line` to send
    a string to the CLI in response to an event::
    
     def sendUsername(target):
        if target.username is None:
            raise MissingDeviceParameter(target, '%s username undefined' % target.name)

        log.debug("sending username  [%s] ..." % target.username)
        target.send_line(target.username)

    
     

    '''    
    
    #processResponseg = None
    
    def __init__(self, name, driver=None, username = None, password = None, protocol='ssh', port=22, hops = []):
        log.debug("[%s] ctor" % name)
        self.name = name
        self.username = username
        self.password = password
        self.protocol = protocol
        self.port = port
        self.hops = hops
        self.loggedin = False
        
        self.eventCb = {}
        self.prompt = {}

        # the finite state machine
        self.state = 'GROUND'

        if not driver:
            self.driver = Driver.get('common')
        else:
            self.driver = driver

        self.set_driver(self.driver.name)
        
    # TODO: return the device url
    def __str__(self):
        return self.name

    def __repr__(self):
        return 'device:' + self.name

    def __getattr__(self, attrname):
        if attrname == 'driver':
            raise AttributeError(attrname)
        else:
            #log.debug("[%s] delegating search for [%s] to [%s]" % (self, attrname, self.driver))
            try:
                return getattr(self.driver, attrname)
            except AttributeError:
                raise AttributeError(attrname)


    def get_driver(self):
        return self.driver.name

    def set_driver(self, driverName):
        '''
        Initialize the device object with the FSM associated with `driverName` 
        '''
        self.driver = Driver.get(driverName)
        
        # Map (input_symbol, current_state) --> (action, next_state).
        self.state_transitions = {}
        # Map (current_state) --> (action, next_state).
        self.state_transitions_any = {}
        self.input_transitions_any = {}
        self.default_transition = None

        self.patternMap = {'*':{}}
        buildPatternsList(self)

        self.set_default_transition(defaultEventHandler, None)
        
        # simply ignore 'prompt-match' on any state
        #self.add_input_any('prompt-match')


    def enable_prompt_discovery(self):
        """
        Match the output device against the promptRegexp pattern and set the device prompt
        """
        self.on_event('timeout', discoverPromptCallback)
        self.on_event('prompt-match', discoverPromptCallback)
        
        # add the cached prompt ...
        if cache_enabled():
            prompt = get_cached_prompt(self)
            if prompt:
                log.debug('[%s] found cached [%s] prompt [%s]' % (self.name, self.state, prompt.prompt))
                self.prompt[self.state] = Prompt(prompt.prompt, tentative=True)
                self.add_expect_pattern('prompt-match', getExactStringForMatch(prompt.prompt), self.state)
                self.discoveryCounter = 0

            else:
                log.debug('[%s] - [%s]: no prompt cached' % (self.name, self.state))
            
        #self.expect(lambda d: d.currentEvent.name == 'timeout' or d.currentEvent.name == 'prompt-match')

    def interaction_log(self):
        return self.esession.logfile.getvalue()

    def is_connected(self):
        '''
        If the device is connected return True 
        '''
        return self.loggedin
        
    def where_am_i(self):
        '''
        return the hop device actually connected. 
        '''
        from pyco.expectsession import SOURCE_HOST
        
        if self.is_connected():
            return self
        
        for d in reversed(self.hops):
            log.debug("checking if [%s] is connected" % d.name)
            if d.is_connected():
                return d
    
        return SOURCE_HOST
    
    def close(self):
        
        if hasattr(self, 'esession'):
            if self.currentEvent.name != 'eof':
                self.esession.close()

        self.state = 'GROUND'
        

    def discover_prompt_with_regexp(self, regexp, state='*'):
        '''
        Use regexp as a hint for prompt discovery 
        Add the guard \'\\\\r\\\\n\' to the begin of prompt regexp
        '''
        
        self.add_event_action("prompt-match", '\r\n' + regexp, state)
        self.on_event('prompt-match', discoverPromptCallback)

        
    def get_prompt(self):
        '''
        Get the current device prompt
        '''
        return self.prompt[self.state].value


    def prompt_discovered(self):
        if self.state in self.prompt:
            return self.prompt[self.state].isFinal()
        return False
        
       
    def get_event(self,pattern):
        '''
        The event associated with the pattern argument
        '''
        try:
            return self.patternMap[self.state][pattern]
        except:
            # TODO: raise an exception if event not found
            return self.patternMap['*'][pattern]


    def connect_command(self, clientDevice):
        
        for ep in iter_entry_points(group='pyco.plugin', name=None):
            log.debug("found [%s] plugin into module [%s]" % (ep.name, ep.module_name))
            authFunction = ep.load()
            if authFunction(self):
                break

        
        if self.protocol == 'ssh':
            # the username must be defined for ssh connections
            if self.username == None:
                raise MissingDeviceParameter(self, '%s username undefined' % self.name)
            try:
                command = clientDevice.sshCommand
            except:
                command = 'ssh ${device.username}@${device.name}'
                
        elif self.protocol == 'telnet':
            
            try:
                command = clientDevice.telnetCommand
            except:
                command = 'telnet ${device.name} ${device.port}'   
        else:
            raise UnsupportedProtocol(self, 'unsupported protocol: %s' % self.protocol)

        template = Template(command)

        clicommand = StringIO()
        context = Context(clicommand, device=self)

        template.render_context(context)
        
        return clicommand.getvalue()
 
        
    def has_event_handlers(self, event):
        return event.name in self.eventCb

    def get_event_handlers(self, event):
        return self.eventCb[event.name]
    
    def on_event(self, eventName, callback):
        log.debug("[%s] adding [%s] for [%s] event" % (self.name, callback, eventName))
        try:
            if not callback in self.eventCb[eventName]:
                self.eventCb[eventName].append(callback)
        except:
            self.eventCb[eventName] = [callback]

    def remove_event_handler(self, eventName, callback):
        log.debug("[%s] removing [%s] event handler [%s]" % (self.name, eventName, callback))
        try:
            self.eventCb[eventName].remove(callback)
        except:
            log.debug("[%s] not found [%s] event handler [%s]" % (self.name, eventName, callback))
        
    def login(self):
        """
        open a network connection using protocol. Currently supported protocols are telnet and ssh.
        If login has succeeded the device is in USER_PROMPT state and it is ready for consuming commands
        """
        from pyco.expectsession import ExpectSession
        log.debug("%s login ..." % self.name)
        self.esession = ExpectSession(self.hops,self)
        self.currentEvent = Event('do-nothing-event')
        
        log.debug("[%s] session: [%s]" % (self.name, self.esession))
        
        try:
            self.esession.login()
        except ExpectException as e:
            # something go wrong, try to find the last connected hop in the path
            log.info("[%s]: in login phase got [%s] error" % (e.device.name ,e.__class__))
            log.debug("full interaction: [%s]" % e.interaction_log)
            raise e
            
        self.clear_buffer()
        
        if self.state == 'GROUND' or self.currentEvent.isTimeout():
            raise LoginFailed(self, 'unable to connect: %s' % self.currentEvent.name)
        else:
            log.debug("%s logged in !!! ..." % self.name)
        

            
        
    def expect(self, checkPoint):
        self.esession.patternMatch(self, checkPoint, [], self.maxWait)
        

    def send_line(self, stringValue):
        """
        simply send ``stringValue`` to the device CLI assuming that the device object is connected::
            
            self.is_connected() == True
        """
        
        log.debug('generating event [%s]' % stringValue)
        
        event = Event(stringValue)
        self.process(event)
        
        log.debug("[%s] sending [%s]" % (self, stringValue))
        self.esession.send_line(stringValue)
        
    def __call__(self, command):
        return self.send(command)
            
    def send(self, script_or_template, param_map=None):
        '''
        Send the template script or a plain script to the device and return the command output.
        
        If the device is not connected first a :py:meth:`pyco.device.Device.login` is executed
        
        `param_map` is a dictionary containing the `key-value` entries used for creating the plain script from the template:
        the key dictionary is the template keyword ${`key`} and `value` is the corresponding substituted value.
        
        if `param_map` is not defined it is assumed that `script_or_template` is a plain script and no substitution is performed.
        
        '''
        
        if self.state == 'GROUND':
            self.login()

        if param_map:
            template = Template(script_or_template)
    
            clicommand = StringIO()
            context = Context(clicommand, **param_map)
    
            template.render_context(context)
            
            command = clicommand.getvalue()
        else:
            command = script_or_template
            
        out = ''
        for line in command.split('\n'):
            log.debug('[%s]: sending line [%s]' % (self.name, line))
            if out != '':
                out += '\n'
            out += self.process_single_line(line)
            
        return out 

    def process_single_line(self, command):
        '''
        Send the command string to the device and return the command output.
        
        After sending the command with :py:meth:`pyco.device.Device.send_line` activate a `processResponse` loop and awaits one of
        the expected results or a timeout.
        
        '''
        #self.clear_buffer()
        self.send_line(command)

        def runUntilPromptMatchOrTimeout(device):
            #return device.currentEvent.name == 'timeout' or device.currentEvent.name == 'prompt-match' or device.currentEvent.name.endswith('_prompt')
            return device.currentEvent.name == 'timeout' or device.currentEvent.name == 'prompt-match' or device.currentEvent.name.endswith('_prompt')

        out = self.esession.processResponse(self, runUntilPromptMatchOrTimeout)
        
        if self.currentEvent.name == 'timeout' and self.discoverPrompt == True:
            
            if not hasattr(self, 'promptPattern') and hasattr(self, 'rediscoverPrompt') and self.rediscoverPrompt:
            
                # rediscover the prompt
                log.debug("[%s] discovering again the prompt ..." % self.name)
                tentativePrompt = out.split('\r\n')[-1]
                log.debug('[%s] taking last line as tentativePrompt: [%s]' % (self.name, tentativePrompt))
                self.enable_prompt_discovery()
                discoverPromptCallback(self, tentativePrompt)
            else:
                raise ConnectionTimedOut(self, 'prompt not hooked')

        # TODO: to be evaluated if this check is useful   
        if self.checkIfOutputComplete == True:
            log.debug("Checking if [%s] response [%s] is complete" % (command,out))
            prevOut = None
            while out != prevOut:
                self.clear_buffer()
                log.debug("[%s] == [%s]" % (prevOut,out))
                prevOut = out
                currOut = self.esession.processResponse(self, runUntilPromptMatchOrTimeout)
                if prevOut == None:
                    out = currOut
                else:
                    out = prevOut + currOut
                log.debug("Rechecking if [%s] response [%s] is complete" % (command,out))
        
        if out.startswith(command):
            out = out.replace(command.replace('\n','\r\n'), '', 1).strip('\r\n')  
        log.info("[%s:%s]: captured response [%s]" % (self.name, command, out))
        
        return out

    def clear_buffer(self):
        log.debug('clearing buffer ...')
        
        try:
            # expect some time (default: 2 seconds) the arrivals of terminal characters and then clears the buffer 
            time.sleep(self.waitBeforeClearingBuffer)
            self.esession.pipe.expect('.*', timeout=1)
            
        except Exception as e:
            log.debug("[%s] clear_buffer timeout: cleared expect buffer (%s)" % (self.name, e.__class__))
            log.debug(e)


    def add_transition (self, input_symbol, state, action=None, next_state=None):

        """This adds a transition that associates:

                (input_symbol, current_state) --> (action, next_state)

        The action may be set to None in which case the process() method will
        ignore the action and only set the next_state. The next_state may be
        set to None in which case the current state will be unchanged.

        You can also set transitions for a list of symbols by using
        add_transition_list(). """

        if next_state is None:
            next_state = state
        self.state_transitions[(input_symbol, state)] = (action, next_state)

    def add_transition_list (self, list_input_symbols, state, action=None, next_state=None):

        """This adds the same transition for a list of input symbols.
        You can pass a list or a string. Note that it is handy to use
        string.digits, string.whitespace, string.letters, etc. to add
        transitions that match character classes.

        The action may be set to None in which case the process() method will
        ignore the action and only set the next_state. The next_state may be
        set to None in which case the current state will be unchanged. """

        if next_state is None:
            next_state = state
        for input_symbol in list_input_symbols:
            self.add_transition (input_symbol, state, action, next_state)

    def add_transition_any (self, state, action=None, next_state=None):

        """This adds a transition that associates:

                (current_state) --> (action, next_state)

        That is, any input symbol will match the current state.
        The process() method checks the "any" state associations after it first
        checks for an exact match of (input_symbol, current_state).

        The action may be set to None in which case the process() method will
        ignore the action and only set the next_state. The next_state may be
        set to None in which case the current state will be unchanged. """

        if next_state is None:
            next_state = state
        self.state_transitions_any [state] = (action, next_state)

    def add_input_any (self, input_symbol, action=None, next_state=None):

        """This adds a transition that associates:

                (input_symbol) --> (action, next_state)

        That is, the input symbol will trigger a transition in any state.
        The process() method checks the input_symbol in "any state" associations after it
        checks for a match of transition_any

        The action may be set to None in which case the process() method will
        ignore the action and only set the next_state. The next_state may be
        set to None in which case the current state will be unchanged. """

        self.input_transitions_any [input_symbol] = (action, next_state)


    def set_default_transition (self, action, next_state):

        """This sets the default transition. This defines an action and
        next_state if the ExtFSM cannot find the input symbol and the current
        state in the transition list and if the ExtFSM cannot find the
        current_state in the transition_any list. This is useful as a final
        fall-through state for catching errors and undefined states.

        The default transition can be removed by setting the attribute
        default_transition to None. """

        self.default_transition = (action, next_state)

    def get_transition (self, input_symbol, state):

        """This returns (action, next state) given an input_symbol and state.
        This does not modify the ExtFSM state, so calling this method has no side
        effects. Normally you do not call this method directly. It is called by
        process().

        The sequence of steps to check for a defined transition goes from the
        most specific to the least specific.

        1. Check state_transitions[] that match exactly the tuple,
            (input_symbol, state)

        2. Check state_transitions_any[] that match (state)
            In other words, match a specific state and ANY input_symbol.
            
        3. Check if the input_symbol has a (action, next_state) association 
            in any state 

        4. Check if the default_transition is defined.
            This catches any input_symbol and any state.
            This is a handler for errors, undefined states, or defaults.

        5. No transition was defined. If we get here then raise an exception.
        """

        if (input_symbol, state) in self.state_transitions:
            return self.state_transitions[(input_symbol, state)]
        elif state in self.state_transitions_any:
            return self.state_transitions_any[state]
        elif input_symbol in self.input_transitions_any:
            return self.input_transitions_any[input_symbol]
        elif self.default_transition is not None:
            return self.default_transition
        else:
            raise FSMException ('Transition is undefined: (%s, %s).' %
                (str(input_symbol), str(state)) )

    def process (self, event, ext=True):

        """This is the main method that you call to process input. This may
        cause the driver to change state and call an action. The action callable
        is invoked with device object argument as a first parameter. This method calls
        get_transition() to find the action and next_state associated with the
        input_symbol and current_state. If the action is None then the action
        is not called and only the current state is changed. This method
        processes one complete input symbol. You can process a list of symbols
        (or a string) by calling process_list(). """

        if event.isActive():
            
            # disactive the event
            event.stopPropagation()
            
            input_symbol = event.name
            #self.input_symbol = input_symbol.name
            (action, next_state) = self.get_transition (input_symbol, self.state)
            log.debug("selected transition [event:%s,beginState:%s] -> [action:%s, endState:%s]" % (input_symbol, self.state, action, next_state))
            
            stateChanged = False
            
            if next_state != None:
                log.debug("transition activated for [%s,%s] -> [%s]" % (input_symbol, self.state, next_state))
                stateChanged = (self.state != next_state)
                self.state = next_state
                
            if action is not None:
                log.debug("[%s]: executing [%s] action [%s]" % (self.name, input_symbol, str(action)))
                action (self)
                
            if stateChanged:
                log.debug('generating event [%s]' % self.state.lower())
                self.currentEvent = Event(self.state.lower())
                self.process(self.currentEvent,ext=False)
           
            return stateChanged

    def process_list (self, input_symbols):

        """This takes a list and sends each element to process(). The list may
        be a string or any iterable object. """

        for s in input_symbols:
            self.process (s)

    def patterns(self, state):
        '''
        Return the pattern list to match the device output 
        '''
        try:
            return list(self.patternMap[state].keys()) + list(self.patternMap['*'].keys())
        except:
            return list(self.patternMap['*'].keys())

    def add_event_action(self, event, pattern=None, beginState=['*'], endState=None, action=None):
        '''
        Add a pattern to be matched in the FSM state. If the pattern is matched then the corresponding event is generated.
        
        If pattern is None only a transition is configured
        '''
        if isinstance(beginState, str):
            beginState = [beginState]
        
        for state in beginState:
            
            if not pattern or pattern == '':
                if state == '*':
                    log.debug("[%s]: [%s] event with empty pattern activated in any state" % (self.name, event))
                    self.add_input_any(event, action, endState)
                else:
                    log.debug("[%s] adding transition [%s-%s (action:%s)-%s]" % (self.name, state, event, action, endState))
                    self.add_transition(event, state, action, endState)
                
                continue
            
            try:
                reverseMap = dict([(item[1],item[0]) for item in list(self.patternMap[state].items())])
                self.patternMap[state][pattern] = event
                log.debug('[%s-%s]: configuring [%s] event [%s]' % (self.name, state, pattern, event))
                if event in reverseMap and pattern != reverseMap[event]:
                    log.debug('[%s]: deleting event [%s]' % (self.name, event))
                    del self.patternMap[state][reverseMap[event]]
            except:
                self.patternMap[state] = {pattern:event}

            #  add the transition
            if state == '*':
                log.debug("[%s]: adding pattern driven transition in any state [%s-%s (action:%s)-%s]" % (self.name, state, event, action, endState))
                self.add_input_any(event, action, endState)
            else:
                log.debug("[%s]: adding pattern driven transition [%s-%s (action:%s)-%s]" % (self.name, state, event, action, endState))
                self.add_transition(event, state, action, endState)


    def add_transition_object(self, t):
        '''
        Add the transition argument coded as a dictionary::
        
            suRule = {
                 'beginState' : 'USER_PROMPT',
                 'event': 'su_event',
                 'action' :  sendSuPassword,
                 'endState' : 'USER2_PROMPT'
               }
               
            device.add_transition_object(suRule)

        '''
        self.add_transition(t['event'], t['beginState'], t['action'], t['endState'])    
        
 

    def add_expect_pattern(self, event, pattern, state):
        log.debug("[%s]: adding expect pattern %s, event [%s], state [%s]" % (self.name, [pattern], event, state))
        if not pattern or pattern == '':
            log.warning("[%s]: skipped [%s] event with empty pattern and * state" % (self.name, event))
            return
        
        try:
            self.patternMap[state][pattern] = event
        except:
            self.patternMap[state] = {pattern:event}
            

    def remove_event(self, event, state = '*'):
        reverseMap = dict([(item[1],item[0]) for item in list(self.patternMap[state].items())])
        if event in reverseMap:
            pattern = reverseMap[event]
            self.remove_pattern(pattern, state)
        
    def remove_pattern(self, pattern, state = '*'):
        try:
            del self.patternMap[state][pattern]
        except KeyError:
            log.info('[%s] failed to delete patternMap[%s] entry [%s]: item not found' % (self.name, state, pattern))


# end Device class


def loadConfiguration(cfgfile=cfgFile):
    '''
    Load the pyco configuration file
    '''
    
    import os.path
    if os.path.isfile(cfgfile):
    #try:
        config = ConfigObj(cfgfile, configspec=resource_filename('pyco', 'cfg/pyco_spec.cfg'))
        return reload(config)
    else:
        raise Exception('pyco configuration file not found: ' + cfgfile)
    #except:
    #    raise Exception('pyco configuration file not found: ' + cfgfile)


def load(config):
    '''
    Load the pyco configObj
    '''
    global configObj
    
    pyco_spec = resource_filename('pyco', 'cfg/pyco_spec.cfg')
    
    config.configspec = ConfigObj(pyco_spec)
    
    val = Validator()
    results = config.validate(val)
    
    if results != True:
        for (section_list, key, _) in flatten_errors(config, results):
            if key is not None:
                raise ConfigFileError('The "%s" key in the section "%s" failed validation' % (key, ', '.join(section_list)))
            else:
                raise ConfigFileError('The following section was missing:%s ' % ', '.join(section_list))
    
    configObj = config
    
    for section in list(config.keys()):
        for (key,value) in list(config[section].items()):
            if value is None:
                log.debug("skipping [%s.%s] undefined value" % (section, key))
                continue
            
            try:
                driver = Driver.get(section)
                if key in ['events', 'transitions']:
                    continue
            except DriverNotFound:
                log.debug("creating driver [%s]" % section)
                driver = driverBuilder(section)
                
            log.debug("setting [%s.%s] = [%s]" % (driver,key,value))
            setattr(driver, key, value)
    
    return config       


def reload(config):
    reset()
    load(config)


def reset():
    '''
    Delete the current configuration parameters
    '''
    global DBSession
    DBSession = None
    
    if configObj is None:
        return;
    
    for section in list(configObj.keys()):
        for (key,value) in list(configObj[section].items()):
            log.debug("deleting %s.%s (was %s)" % (section, key, value))
           
            try:
                driver = Driver.get(section)
                if key not in ['events', 'transitions']:
                    try:
                        delattr(driver, key)
                    except AttributeError:
                        log.debug("[%s] attribute [%s] not found (not directly defined?)" % (section,key))
            except DriverNotFound:
                log.error('configuration reset error: [%s] driver not found' % section)
            
                    

def driverBuilder(modelName):
    driver = Driver(modelName)
    Driver.addDriver(driver)
    return driver




class DriverException(Exception):

    """This is the Driver Exception class."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

class DriverNotFound(DriverException):
    pass




class Driver:

    """Driver for the command line configuration"""

    registry = {}

    def __init__(self, name):

        """This creates the Driver. You set the initial state here. The "memory"
        attribute is any object that you want to pass along to the action
        functions. It is not used by the Driver. For parsing you would typically
        pass a list to be used as a stack. """
        self.name = name



    def __str__(self):
        return self.name

    def __repr__(self):
        return 'driver:' + self.name

    def __getattr__(self, attrname):
        if attrname == 'parent':
            raise AttributeError(attrname)
        else:
            #log.debug("[%s] delegating search for [%s] to [%s]" % (self, attrname, self.parent))
            try:
                pDriver = Driver.get(self.parent)
                return getattr(pDriver, attrname)
            except AttributeError:
                raise AttributeError(attrname)
            

    @staticmethod
    def get(driverName):
        try:
            return Driver.registry[driverName]
        except KeyError:
            raise DriverNotFound('%s driver not defined' % driverName)
        
    @staticmethod
    def addDriver(driver):
        Driver.registry[driver.name] = driver
        
  
try:
    from sqlalchemy import create_engine #@UnresolvedImport
    from sqlalchemy import Column #@UnresolvedImport
    from sqlalchemy import String #@UnresolvedImport
    from sqlalchemy.ext.declarative import declarative_base #@UnresolvedImport
    from sqlalchemy.orm import scoped_session #@UnresolvedImport
    from sqlalchemy.orm import sessionmaker #@UnresolvedImport
    from zope.sqlalchemy import ZopeTransactionExtension #@UnresolvedImport
    import transaction #@UnresolvedImport
    import logging
    
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARN)
   
    Base = declarative_base()
    
    class DevicePrompt(Base):
        __tablename__ = 'device_prompt'
        
        device = Column(String, primary_key=True)
        state  = Column(String, primary_key=True)
        prompt = Column(String)
    
        def __init__(self, device ,state, prompt):
            self.device = device
            self.state = state
            self.prompt = prompt
    
    def initialize_sql():
        Base.metadata.bind = engine
        Base.metadata.create_all(engine)

    
    def createDB(url):
        log.debug('db endpoint: [%s]' % url)
        initialize_sql()
        
    def db_url():
        import os.path
        if hasattr(pyco, 'pyco_home'):
            db_url = 'sqlite:///%s/%s' % (pyco.pyco_home, configObj['common']['cache'])
        else:
            db_url = 'sqlite:////tmp/%s' % configObj['common']['cache']
        return db_url
    
    def cache_enabled():
        return DBSession != None
    
    def cache_exists():
        import os.path
        if hasattr(pyco, 'pyco_home'):
            db_file = '%s/%s' % (pyco.pyco_home, configObj['common']['cache'])
        else:
            db_file = '/tmp/%s' % configObj['common']['cache']
        try:
            if configObj['common']['cache']:
                if not os.path.isfile(db_file):
                    log.debug('creating cache [%s] ...' % db_file)
                    createDB('sqlite://%s' % db_file)
        except Exception as e:
            log.info('prompt cache is not enabled: %s' % e)
    
    
    def get_cached_prompt(target):
        log.debug('[%s] state [%s]: getting cached prompt' % (target.name, target.state))
        try:
            session = DBSession()
            prompt = session.query(DevicePrompt).get((target.name,target.state))
            session.close()
            return prompt
        except Exception as e:
            log.debug('no prompt cached: %s' % e)
            return None
    
    def save_cached_prompt(target):
        log.debug('[%s] state [%s]: caching prompt [%s]' % (target.name, target.state, target.prompt[target.state].value))
        try:
            session = DBSession()
            
            transaction.begin()
            prompt = session.query(DevicePrompt).get((target.name,target.state))
            if prompt:
                prompt.prompt = target.prompt[target.state].value
            else:
                
                log.debug('adding a new prompt to cache')
                prompt = DevicePrompt(target.name, target.state, target.prompt[target.state].value)
                session.add(prompt)
            transaction.commit()
    
            #session.close()
        except Exception as e:
            log.error('no prompt saved: %s' % e)
            
    sql_powered = True
except:
    logging.exception("unable to load sql pluging for caching prompts")
    
    sql_powered = False
    
    def cache_enabled():
        return False
    
    
# finally and only finally load the configuration
loadConfiguration()     


if sql_powered:
    DBSession = None
    if 'cache' in configObj['common']:
        log.debug('creating engine for [%s]' % db_url())
        engine = create_engine(db_url(), echo=False)

        DBSession = scoped_session(sessionmaker(
                                 extension=ZopeTransactionExtension(), bind=engine))
    
        cache_exists()


if __name__ == "__main__":
    import doctest #@UnresolvedImport
    doctest.testmod()

    
        