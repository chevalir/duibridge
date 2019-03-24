#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import serial
import time
import os
import optparse
import sys
import logging
import xml.dom.minidom as minidom
from Queue import Queue
import threading
import paho.mqtt.client as paho
import json



__author__ = 'chevalir'
logger = logging.getLogger("duibridge")
options={}
On_Off = ['Off','On']
from_node = {'init': "HELLO" }
to_node   = {"config_pin" : 'CP', 'force_refresh':"RF", 'force_reload':"RE", "print_eeprom":"TS"}
cmd_cp_default = "CPzzrtyiooizzzozzzzzzzcccccccccccccccccccccccccccccccczzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzcccccccccccccccc"

##  DBG_todo:SP130001     SP130001_OK
##  DBG_todo:SP130001    'SP130001'   'SP{:02}{:04}'.format(13,1)

def format_chacon(t_pin, radiocode, group, action, device):
  ## example : SP03 H 12802190 0 100
  cmd = "SP{:0>2}H{}{}{}{:0>2}".format(t_pin, radiocode, group, action, device )
  return cmd 

## @TODO  manage SP03H128021900000_OK to post status to topic 
def decode_chacon(radio_message): ## TODO to return directly the satus to Jeedom
  return


def build_command(arduino_id, topic, value):
  pconfig = options.pin_config[arduino_id]
  try:
    pin_num = pconfig.all_topics[topic]
    if pin_num == pconfig.transmeter_pin:
      (device, radiocode) = pconfig.t_radio_vpins[topic]
      cmd = format_chacon( pconfig.transmeter_pin, radiocode, 0, value, device-1) ## "SP03H128021900100"
      ## "?>>RFD:"+radiocode+":A:"+value*100+device-1":P:4<<"
      request = Arduino_Request(cmd, cmd+"_OK", {"message":"RFD", "radiocode":radiocode, "action":int(value)==1, "device":(device)} )
      ##"?>>RFD:{}:A:{}:P:4<<".format(radiocode, int(value)*100+device-1))
    else:
      pin_info = pconfig.all_pins[pin_num]
      if pin_info.mode in Pin_def.mode_out_time:
        cmd = "SP{:0>2}{:0>4}".format(pin_num,value)
      if pin_info.mode in Pin_def.mode_out:
        cmd = "SP{:0>2}{}".format(pin_num,value)
      if pin_info.mode in Pin_def.mode_pwm:
        cmd = "SP{:0>2}{:0>3}".format(pin_num,value)
      if pin_info.mode in Pin_def.mode_custom_out:
        cmd = "SP{:0>2}{:0>10}".format(pin_num,value)
      request = Arduino_Request(cmd, cmd+"_OK")
    pass
  except:
    if topic in pconfig.all_topics.keys():
      logger.debug("topic  found "+topic)
    else:
      logger.debug("topic not found "+topic)
    request = Arduino_Request(str(value), str(value)+"_OK")    
    pass
  finally:
    logger.debug("build_command cmd="+request.request)
    pass

  logger.debug("build_command topic: {} value: {} cmd: {}".format(topic, value, request.request))
  return request ## replace by { command:cmd, answer:cmd+"_OK"}

''' -----------------------------------------
'''
class Arduino_Node(object):
  def __init__(self, port, in_queue, arduino_id, out_queue, mqtt):
    self.usb_port=port
    self.request_queue=in_queue
    self.send_queue=out_queue
    self.ID = arduino_id
    self.baud=115200
    self.current_request=None
    self.mqtt=mqtt
    ## Open tread to listen arduino serial port
    thread = threading.Thread(target=self.run, args=())
    thread.daemon = True    # Daemonize thread
    thread.start()          # Start the execution

  def run(self):
    '''Method that runs forever'''
    logger.debug( "Arduino_Node::RUN" )
    self.init_serial_com()
    self.current_request=Arduino_Request("","") ## empty request to initialize 
    self.current_request.received("") ## force close the request
    while True:
      time.sleep(0.1)
      if self.current_request.done(): ## waitting the end of the current request
        if not self.request_queue.empty(): ## check if a cmd need to be sent to arduino
          self.read_queue()
      line = self.read_serial() ## check if the arduino have sothing for us.
      if line != "" and not ( "DBG" in line ):
        if not self.current_request.done() and self.current_request.is_expected(line):
          self.current_request.received(line)
          if not (self.current_request.return_mess==None):
            send_radio_to_topic(self.ID, self.mqtt, 
              self.current_request.return_mess["message"], 
              self.current_request.return_mess["radiocode"], 
              self.current_request.return_mess["device"], 
              self.current_request.return_mess["action"])
          ##logger.debug( "Answer expected")
        else:
          self.send_queue.put(line) ## No task in progress or not expected answer ... 
          logger.debug( "sent to queue")

      ## @TODO Manage expected answer
        
  def reset_with_DTR(self):
    self.SerialPort.flush()
    self.SerialPort.flushInput()
    ## hardware reset using DTR line
    self.SerialPort.setDTR(True)
    time.sleep(0.030) # Read somewhere that 22ms is what the UI does.
    self.SerialPort.setDTR(False)
    time.sleep(0.200)
    self.SerialPort.flush()
    self.SerialPort.flushInput()

  def init_serial_com(self):
    self.SerialPort = serial.Serial(self.usb_port, self.baud, timeout=0.3, xonxoff=0, rtscts=0)
    logger.debug("Arduino {} wainting for HELLO".format(self.ID))
    self.reset_with_DTR()
    line = ""
    checktimer = 0
    while line.find(from_node['init']) < 0:
      time.sleep(1)
      checktimer += 1
      line = self.read_serial()
      line = line.replace('\n', '')
      line = line.replace('\r', '')
      logger.debug("0_Arduino " + str(self.ID) + " >> [" + line + "]")
      if checktimer in [3,6,9,12]:
        self.reset_with_DTR()
      if checktimer > 15:
        logger.error("TIMEOUT d'attente du HELLO de l'arduino " + str(self.ID))
        quit()
    self.SerialPort.flush()
    self.SerialPort.flushInput()
    logger.debug("Arduino " + str(self.ID) + " ready for action")
    ##open serial port @@TODO
  
  def read_serial(self):
    line = self.SerialPort.readline()
    if line != '':
      line = line.replace('\n', '')
      line = line.replace('\r', '')
      logger.debug("read_serial:"+line)
    return line

  def read_queue(self):
    self.current_request = self.request_queue.get(False)
    logger.debug( "read_queue:"+str(self.current_request.request) )
    self.current_request.start()
    if self.current_request.request[0:2] in ['CP', 'SP']:
      self.write_serial(bytes(self.current_request.request))
    else :
      self.current_request.received("") ## to force close the request
    self.request_queue.task_done()

  def write_serial(self, cmd): 
    while len(cmd) > 0:
      self.SerialPort.write(cmd[:64]) ## send the first bloc 64 char    
      cmd = cmd[64:] ## remove the first bloc from the request.
      if len(cmd) > 0:
        time.sleep(0.1) ## delay before next bloc (if any)
      else :
        self.SerialPort.write('\n') ## all blocs sent, now send terminator
    logger.debug( "write_serial end")

''' -----------------------------------------
'''
class Arduino_Request:
  def __init__(self, request, expected_answer, return_value=None):
    self.request = request
    self.answer = ""
    self.status = "INIT"  # INIT|STARTED|OK|KO
    self.timeout = 10
    self.expected = expected_answer
    self.return_mess = return_value
    self.start_time = 0

  def start(self):
    self.start_time = int(time.time())
    self.status = "STARTED"
    return self.request

  def check_status(self):
    ##logger.debug("Arduino_Request start:{} timeout:{} time{}".format(self.start_time, self.timeout, time.time()))
    if (self.status == "STARTED") and (int(time.time()) - self.start_time) >= self.timeout:
      logger.debug("Arduino_Request:{} start:{} timeout:{} time:{}".format(self.request, self.start_time, self.timeout, time.time()))
      self.received("TIMEOUT")
    return self.status

  def done(self):
    return self.status == "OK" or self.check_status() == "KO"

  def is_expected(self, answer):
    if answer == self.expected:
      self.answer = answer
      self.status = "OK"
      return True
    else:
      return False
  
  def received(self, answer):
    if answer == self.expected:
      self.status = "OK"
    else:
      self.status = "KO"
    self.answer = answer




''' -----------------------------------------
'''
class MQTT_Client(paho.Client):
    
  def on_connect(self, mqttc, obj, flags, rc):
    logger.debug("on_connect rc: "+str(rc))

  def on_message(self, mqttc, obj, msg):
    #logger.debug("on_message topic:{} Qos:{} msg:{}".format( msg.topic, msg.qos, msg.payload))
    self.queue.put(build_command(self.arduino_id, msg.topic, msg.payload ))

  def on_publish(self, mqttc, obj, mid):
    ##print("on_publish mid: "+str(obj))
    return

  def on_subscribe(self, mqttc, obj, mid, granted_qos):
    ##print("on_subscribe: "+str(mid)+" "+str(granted_qos))
    return

  def on_log(self, mqttc, obj, level, string):
    ##print(string)
    return

  def publish_message(self, sub_topic , mess ):
    self.publish( sub_topic, mess )

  def subscribe_topics(self, list_of_topic):
    for topic in list_of_topic:
      self.subscribe(topic,0)
      ##logger.debug("MQTT_Client::subscribe_topic :"+str(topic))

  def run(self, arduino_id, broker, qq):
    self.arduino_id = arduino_id
    self.disable_logger()
    self.queue = qq
    self.connect(broker, 1883, 60)
    rc = 0
    while rc == 0:
      rc = self.loop_start()
    return rc



''' -----------------------------------------
'''
class Pin_def:
  digital=1
  analog=2
  custom=3
  mode_status=['r', 'c', 'a', 'y','i','j', range(1,8)]
  mode_out=['o', 'i', 'y' 'e'] 
  mode_out_time=[ 'x', 'v', 'u', 'b' ]
  mode_pwm=[ 'p' ]
  mode_custom_out=['d']

  def __init__(self, **kwds):
    self.__dict__.update(kwds)

  def __repr__(self):
    return str(self.__dict__)

''' -----------------------------------------
'''
class Pin_Config(object):

  def __init__(self, ID, conf_pins_path, conf_ports_path, conf_save_path=None):
    self.id = ID
    self.conf_pins_path=conf_pins_path
    self.conf_ports_path=conf_ports_path
    if conf_save_path==None:
      self.conf_save_path=conf_pins_path
    else:
      self.conf_save_path=conf_save_path

    self.r_radio_vpins = {}
    self.t_radio_vpins = {}
    self.all_pins = {}
    self.all_topics = {}
    self.transmeter_pin = -1
    self.rootNode=''
    self.DPIN=14  #default Digital Pin number
    self.APIN=6   #default Alalog pin number 
    self.CPIN=32  #default Custom pin number
    self.pins_decode={}
    self.cp_list = []  # use to send CP command to arduino CPzzrtyiooizzzzbzzzazzcccczzccccccczzzzzzzczzzzccccccc
    self.port=None

  def load_port_config(self, ports_decode=None):
    try:
      if ports_decode == None:
        logger.debug(self.conf_ports_path + " to load ")
        # Get a file object with write permission.
        file_object = open(self.conf_ports_path, 'r')
        logger.debug(self.conf_ports_path + " loaded ") 
        # Load JSON file data to a python dict object.
        ports_decode = json.load(file_object)
        file_object.close()
      self.port = ports_decode["{}_serial_port".format(self.id)]
    except Exception as e:
      print(e)
      return None
    return 

  def load_pin_config(self, all_pins_decode=None):
    try:
      if all_pins_decode == None:
        logger.debug(self.conf_pins_path + " to load ")
        # Get a file object with write permission.
        file_object = open(self.conf_pins_path, 'r')
        logger.debug(self.conf_pins_path + " loaded ") 
        # Load JSON file data to a python dict object.
        all_pins_decode = json.load(file_object)
        file_object.close()
    except Exception as e:
      print(e)
      return
    if type(all_pins_decode) == list:
      for i in range(len(all_pins_decode)):
        if all_pins_decode[i]['identifier'] == self.id: ## seach A1, or A2, ...
          self.pins_decode = all_pins_decode[i]
    if not self.pins_decode == None:
      self.rootNode = str(self.pins_decode['name'])
      cardType = self.pins_decode['card']
      if cardType.find('UNO'):
        self.DPIN=14
        self.APIN=6
        self.CPIN=32
      for dp in range(self.DPIN + self.APIN + self.CPIN):
        self.cp_list.append('z') 
      self.decode_digital()
      self.decode_ana()
      self.decode_custom()
      self.decode_radio()
    return all_pins_decode

  def decode_digital(self):
    pins = self.pins_decode['digitals']['dpins']
    pin_tag = 'card_pin'
    for pinNum in range(len(pins)):
      thepin = int(str(pins[pinNum][pin_tag]).split(' ', 2)[1])
      mode = pins[pinNum]['mode'].split(";",1)[0]
      topic = pins[pinNum]['topic']
      prefix = pins[pinNum]['prefix']
      if mode=='t':
        self.transmeter_pin = thepin
      full_topic = self.get_topic_prefix(mode, prefix)+topic
      self.all_pins[thepin] = Pin_def(topic=full_topic, mode=mode, type=Pin_def.digital)
      self.all_topics[full_topic]=thepin
      self.cp_list[thepin]=mode
  
  def decode_ana(self):
    pins = self.pins_decode['analog']['apins']
    pin_tag = 'card_pin'
    for pinNum in range(len(pins)):
      thepin = int(str(pins[pinNum][pin_tag]).split(' ', 2)[1])
      mode = pins[pinNum]['mode'].split(";",1)[0]
      topic = pins[pinNum]['topic']
      prefix = pins[pinNum]['prefix']
      full_topic = self.get_topic_prefix(mode, prefix)+topic
      self.all_pins[self.DPIN + thepin] = Pin_def(topic=full_topic, mode=mode, type=Pin_def.digital)
      self.all_topics[full_topic]=self.DPIN + thepin
      self.cp_list[self.DPIN + thepin]=mode

  def decode_custom(self):
    pins = self.pins_decode['custom']['cpins']
    pin_tag = 'custom_pin'
    for pinNum in range(len(pins)):
      thepin = int(pins[pinNum][pin_tag])
      mode = pins[pinNum]['mode'].split(";",1)[0]
      topic = pins[pinNum]['topic']
      prefix = pins[pinNum]['prefix']
      full_topic = self.get_topic_prefix(mode, prefix)+topic
      self.all_pins[self.DPIN + self.APIN + thepin] = Pin_def(topic=full_topic, mode=mode, type=Pin_def.custom)
      self.all_topics[full_topic]=self.DPIN + self.APIN + thepin
      ### self.custom_vpins[self.DPIN + self.APIN + thepin] = (mode, full_topic)
      self.cp_list[self.DPIN + self.APIN + thepin]=mode
    ##logger.debug(self.custom_vpins)

  def decode_radio(self):
    pins = self.pins_decode['radio']['cradio']
    ## @TODO del  pin_tag = 'typeradio'
    for pinNum in range(len(pins)):
      ## @TODO del   prefix_topic=''
      ## @TODO del   typeradio = pins[pinNum][pin_tag].split(";",1)[0]
      mode = pins[pinNum]['mode'].split(";",1)[0]
      topic = pins[pinNum]['topic']
      device = pins[pinNum]['device']
      prefix = pins[pinNum]['prefix']
      '''if len(device) < 2:
        device = "0"+device '''
      # @TODO change device format
      radiocode = pins[pinNum]['radiocode']
  
      if mode in ['r', 'tr']: 
        status_topic = self.get_topic_prefix('r', prefix)+topic
        radiocode_key = '{}#{:0>2}'.format(pins[pinNum]['radiocode'], device)
        self.r_radio_vpins.update({radiocode_key:(device, status_topic)})

      if mode in ['t', 'tr']:
        action_topic = self.get_topic_prefix('t', prefix)+topic
        self.all_topics[action_topic]=self.transmeter_pin
        self.t_radio_vpins.update({action_topic:(device, radiocode)})

  def add_radio_conf(self, radiocode, device, radiocode_key):
    ''' This function is able to add radio configuration line in configuration file
        when it's done it's possible to change the default topic by the true one directly 
        in the configuration editor
    '''
    status_topic='radio/'+radiocode+"/"+str(device)
    self.pins_decode['radio']['cradio'].append({'typeradio': 'H; Chacon DIO', 'radiocode': radiocode, 'topic': status_topic
      , 'prefix': True, 'mode' : 'tr; Trans./Recep.', 'device': device})
    with open(self.conf_save_path, 'w') as outfile:
      json.dump(self.pins_decode, outfile, sort_keys = True, indent = 2)
    status_topic=self.get_topic_prefix('r', True)+status_topic
    logger.info(" new topic added " + status_topic)
    self.r_radio_vpins.update({radiocode_key:(device, status_topic)})

  
  def get_topic_prefix(self, mode, enable):
    global options
    if enable:
      if mode in Pin_def.mode_status: 
        return(self.rootNode+"/"+'status/') 
      else:
        return(self.rootNode+"/"+'action/')
    else:
       return(self.rootNode+"/")

  def get_pin_conf_cmd(self):
    cp = 'CP' + ''.join(self.cp_list)
    return cp


'''-------------------------------'''                      
def send_to_topic(arduino_id, pin_num, value, lmqtt):
  global options

  pconfig = options.pin_config[arduino_id]
  logger.debug( str(arduino_id) + " "+str(pin_num) +" "+ str(value) )
  thePin = int(pin_num)
  try:
    if thePin in pconfig.all_pins.keys():
      pin_info = pconfig.all_pins[thePin]
      if pin_info.mode in Pin_def.mode_status :
        if pin_info.mode in ('r'): ## Radio receptor
          send_radio_to_topic(arduino_id, lmqtt, value)
        else:     
          lmqtt.publish_message(pin_info.topic, value)    
      else:
        logger.error( 'unexpected mode:'+pin_info.mode )
    else :
      logger.info("arduino send value for Pin undefine in conf pin:{} value:{}".format(pin_num,value) )
  except KeyError:
    logger.error( "KeyError not found {} in {}".format(pin_num,  str(pconfig.all_pins.keys())))
  
'''-------------------------------'''                      
def send_radio_to_topic(arduino_id, mqtt, value, radiocode=None, device=None, device_on=None):
  global options

  pconfig = options.pin_config[arduino_id]
  logger.debug("send_radio_to_topic : {} {}".format(value, radiocode))
  if "RFD" in value:
    try:
      if radiocode==None or device==None or device_on==None:
        device_split = value.split(':')
        radiocode = device_split[1]
        device = int (device_split[3]) + 1
        device_on = device > 99
        if device_on :
          device = device - 100
      value = On_Off[int(device_on)]
      radiocode_key = '{}#{:0>2}'.format(radiocode, device) ## search if a topic is define for this device
      if radiocode_key not in pconfig.r_radio_vpins: 
        radiocode_key = '{}#{:0>2}'.format(radiocode, 0) ## search if a topic is define for all devices of this radiocode
        if radiocode_key not in pconfig.r_radio_vpins:
          logger.info("radio code={0} device ={1} not define in config ".format(radiocode, device) )
          pconfig.add_radio_conf(radiocode, device, radiocode_key) ## no config for this radiocode so added with default values
          value = "{0}={1}".format(device, value)      
      (device, topic) = pconfig.r_radio_vpins.get(radiocode_key)
      logger.info("radio code={0} device ={1} topic {2} value: {3} ".format(radiocode_key, device, topic, value) )
      mqtt.publish_message(topic, value)
    except Exception as e:
      logger.error( "send_radio_to_topic exception" )
      logger.error( e )
  return



'''-------------------------------
///            MAIN            /// 
-------------------------------'''  
def main(argv=None):
  global stopMe, options
  myrootpath = os.path.dirname(os.path.realpath(__file__)) + "/"
  print ( "START DEAMON " )
  (options, args) = cli_parser(argv)
  ##print(options)
  write_pid(options.pid_path)
  LOG_FILENAME = myrootpath + '../../../../log/duibridge_daemon'
  formatter = logging.Formatter('%(threadName)s-%(asctime)s| %(levelname)s | %(lineno)d | %(message)s')
  '''filehandler = logging.FileHandler(LOG_FILENAME)
  filehandler.setFormatter(formatter)
  logger.addHandler(filehandler)
  '''

  console = logging.StreamHandler()
  console.setFormatter(formatter)
  logger.addHandler(console)
  logger.setLevel(logging.DEBUG)

  ##sys.stderr = open(LOG_FILENAME + "_stderr", 'a', 1)

  # options.loglevel.upper()
  ##logger.setLevel(logging.getLevelName(options.loglevel.upper()))
  ##
  logger.info("# duiBridged - duinode bridge for Jeedom # loglevel="+options.loglevel)
  
  arduino_id="A1"
  options.pin_config={}
  options.pin_config.update({arduino_id:Pin_Config(arduino_id, options.config_pins_path, options.config_ports_path)})
  print(options.pin_config[arduino_id])
  all_pins_decode = options.pin_config[arduino_id].load_pin_config()
  options.pin_config[arduino_id].load_port_config()
  
  
  options.nodes={}
  rootNode = options.pin_config[arduino_id].rootNode ## TODO manage several arduino
  options.to_arduino_queues={arduino_id:Queue()}
  options.from_arduino_queues={arduino_id:Queue()}

  mqttc1 = MQTT_Client()
  mqttc1.run(arduino_id, "localhost", options.to_arduino_queues[arduino_id])
  aNode = Arduino_Node(options.pin_config[arduino_id].port, options.to_arduino_queues[arduino_id], arduino_id, options.from_arduino_queues[arduino_id], mqttc1)
  options.nodes.update({arduino_id:aNode})
  cp_cmd =options.pin_config[arduino_id].get_pin_conf_cmd()
  request = Arduino_Request(cp_cmd, "CP_OK")
  options.to_arduino_queues[arduino_id].put(request)
  ## subscribe to digital topics if any
  print("----\n\n")
  pin_list = options.pin_config[arduino_id].all_pins.values()
  for pin in pin_list:
    ##(mode, topic) = m_t
    if not ( pin.mode in Pin_def.mode_status ):
      mqttc1.subscribe(pin.topic)
      ##logger.debug('subscribe :'+pin.topic)
    ##else:
      ##logger.debug('not subscribe {} {}:'.format(pin.mode, pin.topic))
  ''' subscribe to radio topics '''
  topics = options.pin_config[arduino_id].t_radio_vpins.keys()
  mqttc1.subscribe_topics(topics)


  while True :
    time.sleep(0.1)
    if not options.from_arduino_queues[arduino_id].empty(): 
      mess = str(options.from_arduino_queues[arduino_id].get(False))
      logger.debug( "from_arduino_queues:"+mess )
      if '>>' in mess[:6]:
        (pin, value) = mess.split(">>")
        value = value.replace("<<", '')
        logger.debug(str(pin) +" "+ str(value))
        send_to_topic(arduino_id, pin, value, mqttc1)
      options.from_arduino_queues[arduino_id].task_done()

    
  logger.debug("THE END")


'''-------------------------------'''
def write_pid(path):
	pid = str(os.getpid())
	logging.info("Writing PID " + pid + " to " + str(path))
	file(str(path), 'w').write("%s\n" % pid)

'''-------------------------------'''
def cli_parser(argv=None):
  parser = optparse.OptionParser("usage: %prog -h   pour l'aide")
  parser.add_option("-l", "--loglevel", dest="loglevel", default="INFO", type="string", help="Log Level (INFO, DEBUG, ERROR")
  ##parser.add_option("-p", "--usb_port", dest="usb_port", default="auto", type="string", help="USB Port (Auto, <Usb port> ")
  parser.add_option("-c", "--config_pins_path", dest="config_pins_path", default=".", type="string", help="config pin file path")
  parser.add_option("-p", "--config_ports_path", dest="config_ports_path", default=".", type="string", help="config ports file path")
  parser.add_option("-i", "--pid", dest="pid_path", default="./duibridge.pid", type="string", help="pid file path")

  return parser.parse_args(argv)

if __name__ == '__main__':
  main()