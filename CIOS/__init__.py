
import fdpexpect
import yaml
import serial
import re
import sys

class CIOSProvisionException(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class CIOSProvision:
    def __init__(self, hostname, IPv4_address, conf_file=None):
        self.hostname = hostname
        self.IPv4_address = IPv4_address

        # Load YAML configuration file
        if conf_file is None:
            self.conf_file = "cios.yml"
        else:
            self.conf_file = conf_file

        print("Using YAML configuration in {0}".format(self.conf_file))
        print("Hostname: {0}\nIPv4 address: {1}\n".format(self.hostname, self.IPv4_address))

        with open(self.conf_file, "r") as cfile:
            self.config = yaml.load(cfile)

        self._initalize_pexpect()


    def __del__(self):
        self.serial.close()


    def _initalize_pexpect(self):

        # Initalize serial port
        self.serial = serial.Serial(self.config['serial']['port'], self.config['serial']['baud'], self.config['serial']['bits'], self.config['serial']['parity'], timeout=None)
        self.fd = self.serial.fileno()

        # Flush current contents of serial Input buffer
        self.serial.flushInput()

        # Pass file descriptor for open serial port to pexpect
        self.switch = fdpexpect.fdspawn(self.fd)

        # Debugging
        # self.switch.logfile = sys.stdout

        # Wait for switch to finish booting and try to get
        # us into enable mode
        timeout_tries = 0

        sys.stdout.write("Waiting for switch")
        sys.stdout.flush()

        while True:
            self.switch.sendline("\r\n")

            res = self._do_expect_prompt(10)

            if res == 0:
                self.switch.sendline('no')
            elif res == 1:
                self.switch.sendline('enable')
            elif res == 2:
                # we're good to go
                break
            elif res == 3:
                self.switch.sendline('exit')
            elif res in [4, 5]:
                raise CIOSProvisionException('Auth prompt encountered. Switch already configured?')
            elif res == 6:
                raise CIOSProvisionException('Unexpected and unhandled prompt received from switch.\n{0}'.format(self.switch.before))
            elif res == 7:
                sys.stdout.write('.')
                sys.stdout.flush()
                # Five minutes of trying (10 * 30)
                if timeout_tries > 30:
                    raise CIOSProvisionException("Timeout after five minutes.  Check switch and serial link.\n{0}".format(self.switch.before))
                timeout_tries += 1

        # Newline after waiting finishes
        print("")

        # Clean up the expect buffer on our way out or things will
        # get confused in future _do_line usage
        self.switch.expect(['$', fdpexpect.TIMEOUT], timeout=1)



    def _do_expect_prompt(self, expect_timeout):

        outcomes = ['\[yes/no\]: ', '\\r\\n[\w\-]+>', '\\r\\n[\w\-]+#', '\\r\\n[\w\-]+\([\w-]+\)#', 'Username:', 'Password:', fdpexpect.EOF, fdpexpect.TIMEOUT]

        # 0: [yes/no]:
        # 1: Switch>
        # 2: Switch#
        # 3: Switch(config)#
        # 4: Username:
        # 5: Password:
        # 6: EOF
        # 7: Timeout

        return self.switch.expect(outcomes, timeout=expect_timeout)


    def _do_line(self, line, expect_timeout=120):

        if line.strip() != "exit" and line.strip() != "configure terminal":
            print (" |  {0}".format(line))

        # Send command
        self.switch.sendline(line)

        # Now wait for the prompt
        res = self._do_expect_prompt(expect_timeout)

        if res in [1, 2, 3]:
            # Did the command cause any warnings?
            errorstring = re.search("\% .*\r\n", self.switch.before)
            if errorstring is not None:
                print("[X] >>> {0}".format(errorstring.group(0).strip()))

        elif res in [4, 5]:
            raise CIOSProvisionException("Auth prompt encountered. Switch already configured?")
        elif res == 6:
            raise CIOSProvisionException("EOF received from serial link.")
        elif res == 7:
            raise CIOSProvisionException("Timeout received while waiting for prompt.\n{0}".format(self.switch.before))


    def _do_block(self, block_start, block_list):

        # Enter configure terminal mode
        self._do_line("configure terminal")

        # Enter sub-mode of conf t as needed
        if block_start is not None:
            print("[*] >>> Starting \"{0}\" configuration:".format(block_start))
            self._do_line(block_start)
        else:
            print("[*] >>> Starting global configuration:")

        for line in block_list:
            self._do_line(line)

        # Exit sub-mode as needed
        if block_start is not None:
            self._do_line("exit")

        # Exit conf t mode
        self._do_line("exit")
        print("[*] ---\n")


    def provision(self):

        # Global
        self._do_block(None, self.config['commands']['global'])

        # Interfaces
        for interface in self.config['commands']['interfaces'].keys():
            self._do_block("interface {0}".format(interface), self.config['commands']['interfaces'][interface])

        # Lines
        for line in self.config['commands']['lines'].keys():
            self._do_block("line {0}".format(line), self.config['commands']['lines'][line])

        # Hostname and IP address
        self._do_block("interface {0}".format(self.config['management interface']['interface']), ["ip address {0} {1}".format(self.IPv4_address, self.config['management interface']['netmask'])])

        self._do_block(None, ["hostname {0}".format(self.hostname)])

        # Finish up
        self._do_block(None, self.config['commands']['finish'])

        # write mem needs to occur outside of 'conf t' mode
        self._do_line("write mem")

        # after write mem do a reload
        print("[*] >>> reloading switch")
        self.switch.sendline("reload")
        self.switch.sendline()
