'''
Created on Mar 21, 2011

@author: adona
'''
import unittest #@UnresolvedImport
from netcube.device import device

from netcube import log

# create logger
log = log.getLogger("test")

class Test(unittest.TestCase):
    
    def testOutputComplete(self):
        
        log.info("testOutputComplete ...")
        h = device('netbox:netbox@localhost')
        h.checkIfOutputComplete = True
        out = h('id')
        self.assertRegexpMatches(out, 'uid=[0-9]+\(netbox\).*')
 

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()