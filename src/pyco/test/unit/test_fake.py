'''
Created on Mar 21, 2011

@author: adona
'''
import unittest #@UnresolvedImport
from pyco.device import device, ConnectionRefused

from pyco import log

# create logger
log = log.getLogger("test")

class Test(unittest.TestCase):
    
    def testFakeOk(self):
        print("should not work")
        log.info("testFakeOk ...")
        h = device('telnet://%s:%s@%s:7777' % ('username', 'secret', 'localhost'))
        h.maxWait = 2
        out = h('id')
        self.assertRegexpMatches(out, 'uid=[0-9]+\(pyco\).*')

 
 

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()