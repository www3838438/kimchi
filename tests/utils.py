#
# Project Kimchi
#
# Copyright IBM, Corp. 2013
#
# Authors:
#  Adam Litke <agl@linux.vnet.ibm.com>
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#

import httplib
import cherrypy
import threading
import time
import os
import sys
import socket
from contextlib import closing
import unittest
import base64

import kimchi.server
import kimchi.model

_ports = {}

fake_user = {'admin': 'letmein!'}

# provide missing unittest decorators and API for python 2.6; these decorators
# do not actually work, just avoid the syntax failure
if sys.version_info[:2] == (2, 6):
    def skipUnless(condition, reason):
        if not condition:
            sys.stderr.write('[expected failure] ')
            raise Exception(reason)
        return lambda obj: obj

    unittest.skipUnless = skipUnless
    unittest.expectedFailure = lambda obj: obj

    def assertGreater(self, a, b, msg=None):
        if not a > b:
            self.fail('%s not greater than %s' % (repr(a), repr(b)))

    def assertGreaterEqual(self, a, b, msg=None):
        if not a >= b:
            self.fail('%s not greater than or equal to %s' % (repr(a), repr(b)))

    def assertIsInstance(self, obj, cls, msg=None):
        if not isinstance(obj, cls):
            self.fail('%s is not an instance of %r' % (repr(obj), cls))

    def assertIn(self, a, b, msg=None):
        if not a in b:
            self.fail("%s is not in %b" % (repr(a), repr(b)))

    def assertNotIn(self, a, b, msg=None):
        if a in b:
            self.fail("%s is in %b" % (repr(a), repr(b)))

    unittest.TestCase.assertGreaterEqual = assertGreaterEqual
    unittest.TestCase.assertGreater = assertGreater
    unittest.TestCase.assertIsInstance = assertIsInstance
    unittest.TestCase.assertIn = assertIn
    unittest.TestCase.assertNotIn = assertNotIn

def get_free_port(name='http'):
    global _ports
    if _ports.get(name) is not None:
        return _ports[name]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with closing(sock):
        try:
            sock.bind(("0.0.0.0", 0))
        except:
            raise Exception("Could not find a free port")
        _ports[name] = sock.getsockname()[1]
        return _ports[name]

def run_server(host, port, ssl_port, test_mode, model=None, environment='development'):
    args = type('_', (object,),
                {'host': host, 'port': port, 'ssl_port': ssl_port,
                 'ssl_cert': '', 'ssl_key': '',
                 'test': test_mode, 'access_log': '/dev/null',
                 'error_log': '/dev/null', 'environment': environment,
                 'log_level': 'debug'})()
    if model is not None:
        setattr(args, 'model', model)
    s = kimchi.server.Server(args)
    t = threading.Thread(target=s.start)
    t.setDaemon(True)
    t.start()
    cherrypy.engine.wait(cherrypy.engine.states.STARTED)
    return s

def silence_server():
    """
    Silence server status messages on stdout
    """
    cherrypy.config.update({"environment": "embedded"})

def running_as_root():
    return os.geteuid() == 0


def _request(conn, path, data, method, headers):
    if headers is None:
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json'}
    if 'AUTHORIZATION' not in headers.keys():
        user, pw = fake_user.items()[0]
        hdr = "Basic " + base64.b64encode("%s:%s" % (user, pw))
        headers['AUTHORIZATION'] = hdr
    conn.request(method, path, data, headers)
    return conn.getresponse()

def request(host, port, path, data=None, method='GET', headers=None):
    conn = httplib.HTTPConnection(host, port)
    return _request(conn, path, data, method, headers)


def https_request(host, port, path, data=None, method='GET', headers=None):
    conn = httplib.HTTPSConnection(host, port)
    return _request(conn, path, data, method, headers)


class RollbackContext(object):
    '''
    A context manager for recording and playing rollback.
    The first exception will be remembered and re-raised after rollback

    Sample usage:
    with RollbackContext() as rollback:
        step1()
        rollback.prependDefer(lambda: undo step1)
        def undoStep2(arg): pass
        step2()
        rollback.prependDefer(undoStep2, arg)
    '''
    def __init__(self, *args):
        self._finally = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        firstException = exc_value

        for undo, args, kwargs in self._finally:
            try:
                undo(*args, **kwargs)
            except Exception as e:
                # keep the earliest exception info
                if not firstException:
                    firstException = e
                    # keep the original traceback info
                    traceback = sys.exc_info()[2]

        # re-raise the earliest exception
        if firstException is not None:
            if type(firstException) is str:
                sys.stderr.write(firstException)
            else:
                raise firstException, None, traceback

    def defer(self, func, *args, **kwargs):
        self._finally.append((func, args, kwargs))

    def prependDefer(self, func, *args, **kwargs):
        self._finally.insert(0, (func, args, kwargs))

def patch_auth():
    """
    Override the authenticate function with a simple test against an
    internal dict of users and passwords.
    """
    def _authenticate(username, password, service="passwd"):
        try:
            return fake_user[username] == password
        except KeyError:
            raise kimchi.model.OperationFailed('Bad login')

    import kimchi.auth
    kimchi.auth.authenticate = _authenticate
