import util
import parser_mysql
import parser_postgres
from scapy.all import *
from sniffer import Sniffer
from collections import namedtuple
from config import pptable

class DatabaseInfo:
	"""Class holds parsed credentials"""
	def __init__(self):
		self.mysql_state = 0	# state of the MySQL protocol
		self.mysql_usr = None
		self.mysql_hsh = None
		self.pg_usr = None
		self.pg_hsh = None
		self.mssql_usr = None
		self.mssql_hsh = None

__name__ = 'Database Sniffer'
class DatabaseSniffer(Sniffer):
	"""Sniffer for parsing database queries/results
	"""

	def __init__(self):
		self.dbi = DatabaseInfo()
		super(DatabaseSniffer,self).__init__('Database')

	def initialize(self):
		"""Initialize sniffer"""
		while True:
			try:
				tmp = raw_input('[!] Sniff database packets from \'%s\'.  Is this correct? '%self.source)
				if 'n' in tmp.lower():
					return None
				break
			except KeyboardInterrupt:
				return None
			except:
				pass

		self.sniff_filter = "tcp and (port 3306 or port 5432 or port 1433) and (src %s or dst %s)"\
							%(self.source,self.source)
		self.sniff = True
		self.sniff_thread.start()
		return self.source
	
	def dump(self, pkt):
		"""Parse packet based on source/dest port. May need
		   to allow this to be configurable in case non-default port
		   is used.
		"""
		# mysql -tested with 5.5.27, 4.1.21
		if pkt[TCP].sport == 3306 or pkt[TCP].dport == 3306:
			self.parse_mysql(pkt[TCP].payload)
		# postgres - tested with 9.0.11 
		elif pkt[TCP].sport == 5432 or pkt[TCP].dport == 5432:
			self.parse_postgres(pkt[TCP].payload)
		# mssql - NOT IMPLEMENTED
		elif pkt[TCP].sport == 1433 or pkt[TCP].dport == 1433:
			self.parse_mssql(pkt[TCP].payload)	

	def parse_mysql(self, raw):
		""" Parse MySQL data; not the best way, but most 
			minimal.
		"""
		raw = util.get_layer_bytes(str(raw))
		if len(raw) <= 0:
			return

		pn = int(raw[3], 16)
		if len(raw) > 75 and pn is 0 and self.dbi.mysql_state is 0:
			# probably a server greeting
			self.log_msg('Protocol: %d'%int(raw[4],16))
			version = ''
			for i in range(5,len(raw)):
				tmp = raw[i]
				if tmp == '00':
					break
				version += tmp.decode('hex')

			salt = ''
			for i in range(0, 8):
				salt += raw[16+i]
			for i in range(0, 13):
				salt += raw[42+i]
			self.log_msg('Version: MySQL %s'%version)
			self.log_msg('Salt: %s'%salt)
			self.dbi.mysql_state = 1
			return
		elif len(raw) > 50 and pn is 1 and self.dbi.mysql_state is 1: 
			# probably a login request
		  	usr = ''
			hsh_idx = 0
			for i in range(36, len(raw)):
				tmp = raw[i]
				if tmp == '00':
					hsh_idx = i+2
					break
				usr += tmp.decode('hex')
			self.dbi.mysql_usr = usr
			pw_hash = ''
			for i in range(hsh_idx,len(raw)):
				pw_hash += raw[i]
			self.dbi.mysql_hsh = pw_hash
			self.log_msg('User: %s'%usr)
			self.log_msg('Password hash: %s'%pw_hash)
			self.dbi.mysql_state = 2
			return
		elif self.dbi.mysql_state is 2 and len(raw) > 10:
			# response to login attempt
		  	if raw[7] == '02' and raw[8] == '00':
				self.log_msg('Login success.')
				self.dbi.mysql_state = 3
			elif raw[5] == '15' and raw[6] == '04':
				self.log_msg('Access denied for \'%s\''%self.dbi.mysql_usr)
				self.dbi.mysql_state = 0
			return
		elif len(raw) is 5:
			# user quit
			if set(raw) == set(['01','00','00','00','01']):
				self.dbi.mysql_usr = None
				self.dbi.mysql_hsh = None
				self.dbi.mysql_state = 0
				self.log_msg('User quit\n')
			return

		if int(raw[3],16) is 0 and len(raw) > 5:
			if int(raw[4],16) is 3:
				# query request
				query = ''
				for i in range(5, len(raw)):
					tmp = raw[i]
					if tmp == '00':
						continue
					if int(tmp,16) >= 20 and int(tmp,16) < 127:
						query += tmp.decode('hex')
			  	self.log_msg('Query: %s'%query)
				self.dbi.mysql_state = 4
			elif int(raw[4],16) is 4:
				# show fields
				field = ''
				for i in range(5, len(raw)):
					tmp = raw[i]
					if tmp == '00':
						continue
					if int(tmp,16) >= 20 and int(tmp,16) < 127:
						field += tmp.decode('hex')
				self.log_msg('Fetching table fields: %s'%field)
				self.dbi.mysql_state = 4
		elif int(raw[3],16) is 1 and len(raw) > 10:
			if parser_mysql.is_okay(raw):
				# Okay packets = ACKs 
				return

			# query response
			response = ''
			# parse query response
			(columns, data) = parser_mysql.get_response(raw)
			if not columns is None and not data is None:
				self.log_msg([x.name for x in columns])
				for entry in data:
					self.log_msg(entry)
				self.dbi.mysql_state = 3
	
	def parse_postgres(self, raw):
		"""Parse PostgreSQL packet.  psql is less insane."""
		raw = util.get_layer_bytes(str(raw))
		if len(raw) <= 1:
			return

		message_type = raw[0]
		if message_type == '70':
			# password message
			plen = parser_postgres.endian_int(raw[1:5])
			password = ''
			for i in xrange(plen-5):
				password += raw[5+i].decode('hex')
			self.log_msg('Password hash: %s'%password)
		elif message_type == '51':
			# simple query
			query = parser_postgres.parse_query(raw)
			self.log_msg('Query: %s'%query)
		elif message_type == '54':
			# query response
			(columns, rows) = parser_postgres.parse_response(raw)
			self.log_msg(columns)
			for row in rows:
				self.log_msg(row)
		elif message_type == '58':
			self.log_msg('User quit.\n')
		elif message_type == '45':
			self.log_msg('Error: %s'%parser_postgres.parse_error(raw))
		elif message_type == '52':
			if not parser_postgres.database_exists(raw):
				self.log_msg('Invalid database.')
		elif message_type == '00':
			# startup/other
			if parser_postgres.is_ssl(raw):
				self.log_msg('SSL request!')
			else:
				startup = parser_postgres.parse_startup(raw)
				self.log_msg('Startup packet:')
				idx = 0
				while idx < len(startup)-1:	
					self.log_msg('\t%s -> %s'%(startup[idx], startup[idx+1]))
					idx += 2

	def parse_mssql(self, raw):
		"""Parse MSSQL packet"""
		pass