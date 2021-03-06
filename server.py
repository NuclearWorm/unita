from flask import Flask  
from flask import render_template
from flask import request, abort, redirect, url_for, jsonify
import os
import re
import sys
import time
# Let's add 2.6 support
try:
    import unittest2 as unittest
    print "Importing unittest2 from the future"
except:
    import unittest
    print "Importing unittest as usual"

import logging
import copy
from gevent import monkey; monkey.patch_all()
from gevent.pywsgi import WSGIServer
from werkzeug.debug import DebuggedApplication
from werkzeug.serving import run_with_reloader
   
DEBUG = True
#DEBUG = False
# Initial value of trigger to stop executing tests
STOP_IT = False
# Directory for temporary files
TMP_DIR = "/tmp/"

# Regexp for description of test suite in web interface
# Should be in main file with unittests
# TODO: add required description to Howto and Readme
desc_re = re.compile(r'WEBtest_description="([^"]+)"')

#      The map how the test are organized
###
### -/home
###       -user
###            -virtual_env                  # with all needed packages
###                        -bin/activate.py
###            -testenv1
###                     -some_internal_path_to_dir
###                                               -tests
###            -testenv2
###                     -some_internal_path_to_dir
###                                               -tests
###            -testenv3
###                     -some_internal_path_to_dir
###                                               -tests


"""
How about this:
conf = {
    "env1": {"dir": "/home/serg/testenv1/internal_path_to_tests/",
             "files": "",
             "only_files" : "",
             "ignore_files": "",
             "recursion": False,
             }
    "env2": {"dir": "/home/serg/testenv2",
             "files": "",
             "only_files" : "",
             "ignore_files": "",
             "recursion": True,
             }
"""

# Environments with test files, each env should fit some directory with tests
# TODO: do it configurable from web interface
DIRS = [
   'testenv1',
   'testenv2',
   #'testenv3',
   #'testenv4',
]
# Directory of virtualenv where all packages are located
# TODO: make it optional, not everyone uses virtualenv
VIRT_ENV = os.path.join(os.environ["HOME"], 'virtual_env')

# { "virt2": [], "virt": [], "test1": [] }
RUNNING = dict([(env, []) for env in DIRS])

# { "virt2": {}, "virt": {}, "test1": {} }
SUITE_RESULT = dict([(env, {'running': False, 'finished': False, }) for env in DIRS])

HOME = os.environ['HOME'] # yeah, I'm joking
# "/home/he/virt2/tplant/qadir"
# Internal path within the environment
# TODO: make it configurable
hashlama = lambda x: os.path.join(os.environ['HOME'], x, "internal_path_to_tests")
ENVS = map(hashlama, DIRS)
# /home/user/testenv1/internal_path_to_tests

# TODO: make all these paths simpler, simpler, SIMPLERRRR!
logging.basicConfig(level=logging.DEBUG,
                    format='[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s',
                    datefmt='%d/%b/%Y %H:%M:%S',
                    #filename='logfile.log',
                    #filemode='a',
                    #maxBytes=50000,
                    #backupCount=2,
                    )
log = logging.getLogger(__name__)
if log.name == "__main__":
   def lg(*x): print (", ".join([str(i) for i in x]))
   def li(*x): print (", ".join([str(i) for i in x]))
else:
   lg = lambda *x: log.debug(", ".join([str(i) for i in x]))
   li = lambda *x: log.info(", ".join([str(i) for i in x]))


# Regexps for finding files with tests, let's search only *.py
test_regex = re.compile(r"\w+\.py$")

app = Flask(__name__)

# Debug log - is log file where any debug info during the test could be saved
# This function clears it
# TODO: do something with this :(
def empty_debuglog():
   with open(os.path.join(os.getcwd(), "debuglog"), "w") as dblog:
      dblog.write("")

# Make jsonifying be better
def json_fix(x):
   if not x: return []
   new_x = []
   for i in x:
      new_x += (i[0].__repr__(), i[1])
   return new_x

# Let's search files
def find_tests_in_env(env):
   full_path = hashlama(env)
   lg("Full path:", full_path)
   files = [f for f in os.listdir(full_path) if test_regex.match(f) and "template_test" not in f]
   lg("Files:", ",".join(files))
   d_files = []
   for file in files:
      d_file = {}
      d_file['abs_path'] = os.path.join(full_path, file)
      d_file['name'] = file
      d_file['desc'] = desc_re.search(open(d_file['abs_path']).read()).group(1) if desc_re.search(open(d_file['abs_path']).read()) else None
      if d_file['desc']:
         d_files.append(d_file)
   return d_files

# Let's load all the cases from the file
def load_cases(file):
   home_env = "/".join(os.path.dirname(file).split("/")[:-2])
   li("Load cases from", file)
   #print "Paths", os.path.dirname(file), os.path.basename(file)
   lg("Previous sys.path", sys.path)
   # old_sys_path = sys.path[:]
   lg("Executing virtualenv for", file)
   module_name = os.path.basename(file).split(".")[0]
   #activate_this = home_env + '/bin/activate_this.py'
   activate_this = VIRT_ENV + '/bin/activate_this.py'
   execfile(activate_this, dict(__file__=activate_this))
   lg("Executed: ", activate_this)
   lg("Now new sys.path", sys.path)
   tl=unittest.TestLoader()
   lg("TestLoader", tl)
   ts = tl.discover(os.path.dirname(file), pattern=os.path.basename(file), top_level_dir=os.path.dirname(file))
   lg("Test cases object", ts)
   # TODO: support Suite names and display them in web
   ulist = []
   for i in xrange(len(ts._tests[0]._tests)):
      ulist += ts._tests[0]._tests[i]._tests
   lg("Test cases", ulist)
   lg("Deleting ", module_name)
   del sys.modules[module_name]
   lg("Deactivating virtual env...")
   sys.path = [i for i in sys.path if home_env not in i]
   lg("Deactivated sys.path", sys.path)
   return ulist

# Run one test
def run_real_test(env, test):
   file_name = test.split(".")[0]
   home_env = os.path.join(HOME, env)
   full_path = os.path.join(hashlama(env), file_name) + ".py"
   activate_this = VIRT_ENV + '/bin/activate_this.py'
   #activate_this = home_env + '/bin/activate_this.py'
   # Activating virtualenv
   execfile(activate_this, dict(__file__=activate_this))
   print "Fulle_path", full_path

   tl=unittest.TestLoader()
   lg("TestLoader", tl)
   ts = tl.discover(os.path.dirname(full_path), pattern=os.path.basename(full_path), top_level_dir=os.path.dirname(full_path))
   ulist = []
   for i in xrange(len(ts._tests[0]._tests)):
      ulist += ts._tests[0]._tests[i]._tests
   test4run = [test_ for test_ in ulist if test_.id() == test][0]
   print "Test for running choosen:", test4run
   #result = test4run.run()
   
   print "Empty(?) Running:", RUNNING
   RUNNING[env].append(test4run)
   print "Running:", RUNNING
   print "Debug Running:", [i.id() for i in RUNNING[env]], [i._testMethodName for i in RUNNING[env]]
   res = unittest.TestResult()
   tmp_suite = unittest.TestSuite()
   tmp_suite.addTest(test4run)
   tmp_suite.run(res)
   
   RUNNING[env].remove(test4run)
   print "Running:", RUNNING
   
   print "Test running finished!"
   print res.__dict__
   print "Test was successful:", str(res.wasSuccessful())
   
   # Do we need debug log?
   print "Debuglog?", os.path.join(os.getcwd(), "debuglog")
   try:
      with open(os.path.join(os.getcwd(), "debuglog")) as dblog:
         text = dblog.read()
   except Exception as e:
      lg("Exception when trying open file", os.path.join(os.getcwd(), "debuglog"), str(e))
      text = ''
   
   # De-activating virtualenv
   del sys.modules[file_name]
   sys.path = [i for i in sys.path if home_env not in i]
   return render_template("try.html", res=res, text = text)
   #return res, text
   
# Run suite of test, sequentially.
def run_real_test_suite(env, tests):
   #global SUITE_RESULT
   #SUITE_RESULT = dict([(env, {'running': False, 'finished': False, }) for env in DIRS])
   
   global STOP_IT
   STOP_IT = False
   
   print "Started test suite!", env, tests
   file_name = tests[0].split(".")[0]
   home_env = os.path.join(HOME, env)
   full_path = os.path.join(hashlama(env), file_name) + ".py"
   #activate_this = home_env + '/bin/activate_this.py'
   activate_this = VIRT_ENV + '/bin/activate_this.py'
   # Activating virtualenv
   execfile(activate_this, dict(__file__=activate_this))
   print "Fulle_path", full_path
   
   tl=unittest.TestLoader()
   lg("TestLoader", tl)
   ts = tl.discover(os.path.dirname(full_path), pattern=os.path.basename(full_path), top_level_dir=os.path.dirname(full_path))
   ulist = []
   for i in xrange(len(ts._tests[0]._tests)):
      ulist += ts._tests[0]._tests[i]._tests
   #ulist = ts._tests[0]._tests[0]._tests
   tests4run = [test_ for test_ in ulist if test_.id() in tests]
   print "Test for running choosen:", tests4run
   print "Empty(?) Running:", RUNNING
   RUNNING[env] += tests4run
   print "Running:", RUNNING
   print "Debug Running:", [i.id() for i in RUNNING[env]], [i._testMethodName for i in RUNNING[env]]
   print "SUITE_RESULT", SUITE_RESULT
   SUITE_RESULT[env]['running'] = True
   SUITE_RESULT[env]['finished'] = False
   print "SUITE_RESULT after:", SUITE_RESULT
   
   for test in tests4run:
       SUITE_RESULT[env][test.id()] = {
         'wasSuccessful': None,
         'failures': [],
         'errors': [],
         'running': False,
         'finished': False,
         'results': None,
         }
   print "SUITE_RESULT after after:", SUITE_RESULT
   
   for test in tests4run:
      print "Loop start: STOP_IT =", STOP_IT
      if not STOP_IT:
         tmp_suite = unittest.TestSuite()
         res = unittest.TestResult()
         tmp_suite.addTest(test)
         SUITE_RESULT[env][test.id()]['running'] = True  
         print "SUITE_RESULT test:", SUITE_RESULT[env][test.id()]
         empty_debuglog()
         # Running
         tmp_suite.run(res)
         #
         RUNNING[env].remove(test)
         with open(os.path.join(os.getcwd(), "debuglog")) as dblog:
            text = dblog.read()
         SUITE_RESULT[env].update({
            test.id() : {'wasSuccessful':res.wasSuccessful(),
               'failures': res.failures,
               'errors': res.errors,
               'running' : False,
               'finished': True,
               'results': res,
               'text': text,},
         })  
      else: break
   
   SUITE_RESULT[env]['running'] = False
   SUITE_RESULT[env]['finished'] = True
   for test in tests4run:
      if test in RUNNING[env]: RUNNING[env].remove(test)
   
   print "Running:", RUNNING
   #print "SUITE_RESULT:", SUITE_RESULT
   print "Test running finished!"
   
   # Do we need debug log?
   print "Debuglog?", os.path.join(os.getcwd(), "debuglog")
   with open(os.path.join(os.getcwd(), "debuglog")) as dblog:
      text = dblog.read()
   
   # De-activating virtualenv
   del sys.modules[file_name]
   sys.path = [i for i in sys.path if home_env not in i]
   
   
## Routes


@app.route('/run_test', methods=['POST', 'GET'])
def run_test():
   print "POST/GET request:", request.form
   if request.method == 'POST':
      print "Running test", request.form['test4run'], "within virtualenv", request.form['env']
      reply=run_real_test(request.form['env'], request.form['test4run'])
   if request.method == 'GET':
      return "Not GET!!! POST!!!"
   return reply

@app.route('/run_test_suite', methods=['POST'])
def run_test_suite():
   print "POST/GET request:", request.form
   if request.method == 'POST':
      print "Running testsuite", request.form['test4run'], "within virtualenv", request.form['env']
      tests = [t for t in request.form['test4run'].split(",")]
      run_real_test_suite(request.form['env'], tests)
   return "started"   

@app.route('/get_test_info', methods=['GET'])
def get_test_info():
   env = request.args.get('env','')
   test = request.args.get('test','')
   print "GET request:", env, test
   try:
      if not env or not test or request.method != 'GET': return "ERROR! Give env and test!"
      res = SUITE_RESULT[env][test]['results']
      print "Res is here:",res, res.__class__
      text = SUITE_RESULT[env][test]['text']
      html = render_template("try.html", res=res, text = text)
      if res.wasSuccessful():
         status = "passed"
      elif res.failures:
         status = "failed"
      elif res.errors:
         status = "errored"
      else:
         status = "unknown"
      return jsonify({"status" : status, "html": html})
   except Exception, e:
      print "Excepted get_test_info", str(e)
      return "ERROR!"

@app.route('/get_env_test_status', methods=['POST', 'GET'])
def get_env_test_status():
   try:
      print "POST/GET request for all info:"
      env = request.args.get('env','')
      print "getting tests for env", env
      id_list = [test.id() for test in RUNNING[env]]
      reply = jsonify({env: id_list})
      print "'" + str(reply) + "'"
      return reply
   except Exception, e:
      print "Excepted gettestenvstatus",str(e)

@app.route('/get_env_testsuite_status', methods=['GET'])
def get_env_testsuite_status():
   
   try:
      if request.method == 'GET':
         env = request.args.get('env','')
      #print SUITE_RESULT[env], SUITE_RESULT[env].__class__
      #res = SUITE_RESULT[env].copy()
      res = copy.deepcopy(SUITE_RESULT[env])
      # Make some prepares for jsonify
      for key in res:
         if key in ["finished", "running"]: continue
         res[key]['failures'] = json_fix(res[key]['failures'])
         res[key]['errors'] = json_fix(res[key]['errors'])
         res[key]['results'] = str(res[key]['results'])
      
      #print "OPPA: reply in json: ", res
      reply = jsonify(res)
      return reply
   except Exception, e:
      print "Excepted get_env_testsuite_status",str(e)

@app.route('/stop_test', methods=['POST'])
def stop_test():
   print " --- SERVER --- Let's stop all!"
   global STOP_IT
   STOP_IT = True
   return "stopped"
   


@app.route("/<env>")
@app.route("/<env>/<tfile>")
def tests_from(env=None, tfile=None):
   print "ENV:", env, tfile, request
   
   if request.method == 'POST':
      print "You're falling to tests_from function", request.form
    
   if env in DIRS:
      if tfile is None:
         test_files = find_tests_in_env(env) 
         return render_template("env.html", envs=DIRS, testenv=env, tests=test_files)
      else:
         print "Tfile", tfile, "is not None!!!!"
         test_files = [f['name'] for f in find_tests_in_env(env)]
         tfile = tfile + ".py"
         if tfile in test_files:
            full_path = os.path.join(hashlama(env), tfile)
            test_cases = load_cases(full_path)
            return render_template("tests.html", test_env=env, test_file=tfile, cases=test_cases, envs=DIRS,)
         else:
            abort(404)
   else:
      abort(404)
    

@app.route("/")  
def index():
   lg("Main - /", request)
   tests = {}
   for k, env in enumerate(DIRS):
      test_files = find_tests_in_env(env)
      tests[DIRS[k]] = test_files
   return render_template("index.html", tests=tests, keys=tests.keys(), envs=DIRS)

@run_with_reloader
def run_debug():
   http_server = WSGIServer(('0.0.0.0', 5000), DebuggedApplication(app))
   http_server.serve_forever()


if __name__ == "__main__":  
   #app.run('0.0.0.0', debug=True)
   if DEBUG:
      run_debug()
   else:
      http_server = WSGIServer(('0.0.0.0', 5000), app)
      http_server.serve_forever()
