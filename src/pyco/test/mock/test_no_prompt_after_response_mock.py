'''
Created on Mar 21, 2011

@author: adona
'''
import unittest2 #@UnresolvedImport
from pyco.device import device, ConnectionRefused, ConnectionTimedOut

from pyco import log
import simulator

from fixture import *
from mock import Mock, patch, patch_object, sentinel #@UnresolvedImport

import re #@UnresolvedImport
import sys

if sys.platform != 'win32':
    from pexpect import TIMEOUT, EOF #@UnresolvedImport
    spawnFunction = 'pexpect.spawn'
else:
    from winpexpect import TIMEOUT, EOF #@UnresolvedImport
    spawnFunction = 'winpexpect.winspawn'

# create logger
log = log.getLogger("test")

  
class Test(unittest2.TestCase):

    def setUp(self):
        self.responses = ['Username: ', 'password: ', 'router> ', 'router> ', 
                          'some output\r\n -- More -- \r\n', 'more output \r\n -- More --\r\n', 'more and more\r\n -- More -- \r\n' ]

    @patch(spawnFunction)    
    def testNoPromptAfterResponse(self, MockExpect):
        from pyco.device import cliIsConnected
        log.info("testNoPromptAfterResponse ...")
        cisco = device('telnet://%s:%s@%s' % (cisco1['username'], cisco1['password'], cisco1['name']))
        
        from pyco.actions import sendUsername
        cisco.addEventAction('username-event', pattern='Username: ', action=sendUsername, beginState='GROUND')
        
        MockExpect.side_effect = simulator.side_effect
        simulator.side_effect.responses = self.responses

        out = cisco.login()
    
        self.failUnlessRaises(ConnectionTimedOut, cisco.send, 'show version')

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest2.main()