# -*- coding: utf-8 -*-

import pkg_resources
import os
import subprocess
import logging
from time import gmtime, strftime
import stimela
import stimela.utils

##############################################################################
# Globals
##############################################################################


def report_version():
    # Distutils standard  way to do version numbering
    try:
        __version__ = pkg_resources.require("meerkathi")[0].version
    except pkg_resources.DistributionNotFound:
        __version__ = "dev"
    # perhaps we are in a github with tags; in that case return describe
    path = os.path.dirname(os.path.abspath(__file__))
    try:
        # work round possible unavailability of git -C
        result = subprocess.check_output(
            'cd %s; git describe --tags' % path, shell=True, stderr=subprocess.STDOUT).rstrip().decode()
    except subprocess.CalledProcessError:
        result = None
    if result != None and 'fatal' not in result:
        # will succeed if tags exist
        return result
    else:
        # perhaps we are in a github without tags? Cook something up if so
        try:
            result = subprocess.check_output(
                'cd %s; git rev-parse --short HEAD' % path, shell=True, stderr=subprocess.STDOUT).rstrip().decode()
        except subprocess.CalledProcessError:
            result = None
        if result != None and 'fatal' not in result:
            return __version__+'-'+result
        else:
            # we are probably in an installed version
            return __version__


__version__ = report_version()

# global settings
pckgdir = os.path.dirname(os.path.abspath(__file__))
BASE_MEERKATHI_LOG = "log-meerkathi.txt"
MEERKATHI_LOG = os.path.join(os.getcwd(), BASE_MEERKATHI_LOG)
DEFAULT_CONFIG = os.path.join(
    pckgdir, "sample_configurations", "minimalConfig.yml")
SCHEMA = os.path.join(
    pckgdir, "schema", "schema-{0:s}.yml".format(__version__))

################################################################################
# Logging
################################################################################

import logging.handlers

class DelayedFileHandler(logging.handlers.MemoryHandler):
    """A DelayedFileHandler is a variation on the MemoryHandler. It will buffer up log
    entries until told to stop delaying, then dumps everything into the target file
    and from then on logs continuously. This allows the log file to be switched at startup."""
    def __init__(self, filename, delay=True):
        logging.handlers.MemoryHandler.__init__(self, 100000, target=logging.FileHandler(filename))
        self._delay = delay

    def shouldFlush(self, record):
        return not self._delay

    def setFilename(self, filename, delay=False):
        self._delay = delay
        self.setTarget(logging.FileHandler(filename))
        if not delay:
            self.flush()


LOGGER_NAME = "CARACal"
STIMELA_LOGGER_NAME = "CARACal.Stimela"

log = logging.getLogger(LOGGER_NAME)

# these will be set up by init_logger
log_filehandler = log_console_handler = log_console_formatter = None

def create_logger():
    """ Creates logger and associated objects. Called upon import"""
    global log, log_filehandler

    log.setLevel(logging.DEBUG)
    log.propagate = False

    # init stimela logger as a sublogger
    stimela.logger(STIMELA_LOGGER_NAME, propagate=True, console=False)

    log_filehandler = DelayedFileHandler(MEERKATHI_LOG)

    log_filehandler.setFormatter(stimela.log_boring_formatter)
    log_filehandler.setLevel(logging.DEBUG)

    log.addHandler(log_filehandler)


def init_console_logging(boring=False, debug=False):
    """Sets up console logging"""
    global log_console_handler, log_console_formatter

    log_console_formatter = stimela.log_boring_formatter if boring else stimela.log_colourful_formatter

    log_console_handler = logging.StreamHandler()
    log_console_handler.setLevel(logging.INFO)
    log_console_handler.setFormatter(log_console_formatter)

    # add filter to console handler: block Stimela messages at level <=INFO, unless they're intrinsically interesting
    # (the logfile still gets all the messages)
    if not debug:
        def _console_filter(rec):
            if rec.name.startswith(STIMELA_LOGGER_NAME) and rec.levelno <= logging.INFO:
                return hasattr(rec, 'stimela_subprocess_output') or hasattr(rec, 'stimela_job_state')
            return True
        log_console_handler.addFilter(_console_filter)

    log.addHandler(log_console_handler)


def remove_log_handler(hndl):
    log.removeHandler(hndl)

def add_log_handler(hndl):
    log.addHandler(hndl)

create_logger()


