#! /usr/bin/env python
############################################################################
# Copyright (C) 2002  Paul Rodger <paul@paulrodger.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
############################################################################

"""
Archive and compress old mail in mbox or maildir-format mailboxes.
Website: http://archivemail.sourceforge.net/
"""

import sys

def check_python_version(): 
    """Abort if we are running on python < v2.0"""
    too_old_error = "This program requires python v2.0 or greater."
    try: 
        version = sys.version_info  # we might not even have this function! :)
        if (version[0] < 2):
            print too_old_error
            sys.exit(1)
    except AttributeError:
        print too_old_error
        sys.exit(1)

check_python_version()  # define & run this early because 'atexit' is new

import atexit
import fcntl
import getopt
import mailbox
import os
import rfc822
import signal
import string
import tempfile
import time

# global administrivia 
__version__ = "archivemail v0.2.0"
__cvs_id__ = "$Id$"
__copyright__ = """Copyright (C) 2002  Paul Rodger <paul@paulrodger.com>
This is free software; see the source for copying conditions. There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE."""

_stale = None    # list of files to delete on abnormal exit

############## class definitions ###############

class Stats:
    """Class to collect and print statistics about mailbox archival"""
    __archived = 0
    __mailbox_name = None
    __archive_name = None
    __start_time = 0
    __total = 0

    def __init__(self, mailbox_name, final_archive_name):
        """Constructor for a new set of statistics.

        Arguments: 
        mailbox_name -- filename/dirname of the original mailbox
        final_archive_name -- filename for the final 'mbox' archive, without
                              compression extension (eg .gz)

        """
        assert(mailbox_name)
        assert(final_archive_name)
        self.__start_time = time.time()
        self.__mailbox_name = mailbox_name
        self.__archive_name = final_archive_name + _options.compressor_extension

    def another_message(self):
        """Add one to the internal count of total messages processed"""
        self.__total = self.__total + 1

    def another_archived(self):
        """Add one to the internal count of messages archived"""
        self.__archived = self.__archived + 1

    def display(self):
        """Print statistics about how many messages were archived"""
        end_time = time.time()
        time_seconds = end_time - self.__start_time
        action = "archived"
        if _options.delete_old_mail:
            action = "deleted"
        if _options.dry_run:
            action = "I would have " + action
        print "%s: %s %d of %d message(s) in %.1f seconds" % \
            (self.__mailbox_name, action, self.__archived, self.__total,
            time_seconds)
            

class StaleFiles:
    """Class to keep track of files to be deleted on abnormal exit"""
    archive            = None  # tempfile for messages to be archived
    compressed_archive = None  # compressed version of the above
    procmail_lock      = None  # original_mailbox.lock
    retain             = None  # tempfile for messages to be retained

    def clean(self):
        """Delete any temporary files or lockfiles that exist"""
        if self.procmail_lock:
            vprint("removing stale procmail lock '%s'" % self.procmail_lock)
            try: os.remove(self.procmail_lock)
            except (IOError, OSError): pass
        if self.retain:
            vprint("removing stale retain file '%s'" % self.retain)
            try: os.remove(self.retain)
            except (IOError, OSError): pass
        if self.archive:
            vprint("removing stale archive file '%s'" % self.archive)
            try: os.remove(self.archive)
            except (IOError, OSError): pass
        if self.compressed_archive:
            vprint("removing stale compressed archive file '%s'" %
                self.compressed_archive)
            try: os.remove(self.compressed_archive)
            except (IOError, OSError): pass


class Options:
    """Class to store runtime options, including defaults"""
    archive_suffix       = "_archive"
    compressor           = None
    compressor_extension = None
    days_old_max         = 180
    delete_old_mail      = 0
    dry_run              = 0
    lockfile_attempts    = 5  
    lockfile_extension   = ".lock"
    lockfile_sleep       = 1 
    output_dir           = None
    quiet                = 0
    script_name          = os.path.basename(sys.argv[0])
    use_modify_time      = 0
    verbose              = 0
    warn_duplicates      = 0

    def parse_args(self, args, usage):
        """Set our runtime options from the command-line arguments.

        Arguments:
        args -- this is sys.argv[1:]
        usage -- a usage message to display on '--help' or bad arguments

        Returns the remaining command-line arguments that have not yet been
        parsed as a string.

        """
        try:
            opts, args = getopt.getopt(args, '?IVZd:hmno:qs:vz', 
                             ["bzip2", "compress", "days=", "delete",
                             "dry-run", "gzip", "help", "output-dir=", 
                             "quiet", "suffix", "modify-time", "verbose", 
                             "version", "warn-duplicate"])
        except getopt.error, msg:
            user_error(msg)
        for o, a in opts:
            if o == '--delete':
                self.delete_old_mail = 1
            if o == '--warn-duplicate':
                self.warn_duplicates = 1
            if o in ('-n', '--dry-run'):
                self.dry_run = 1
            if o in ('-d', '--days'):
                self.days_old_max = string.atoi(a)
                if (self.days_old_max < 1):
                    user_error("argument to -d must be greater than zero")
                if (self.days_old_max >= 10000):
                    user_error("argument to -d must be less than 10000")
            if o in ('-o', '--output-dir'):
                if not os.path.isdir(a):
                    user_error("output directory does not exist: '%s'" % a)
                self.output_dir = a
            if o in ('-h', '-?', '--help'):
                print usage
                sys.exit(0)
            if o in ('-q', '--quiet'):
                self.quiet = 1
            if o in ('-m', '--modify-time'):
                self.use_modify_time = 1
            if o in ('-v', '--verbose'):
                self.verbose = 1
            if o in ('-s', '--suffix'):
                self.archive_suffix = a
            if o in ('-V', '--version'):
                print __version__ + "\n\n" + __copyright__
                sys.exit(0)
            if o in ('-z', '--gzip'):
                if (self.compressor):
                    user_error("conflicting compression options")
                self.compressor = "gzip"
            if o in ('-Z', '--compress'):
                if (self.compressor):
                    user_error("conflicting compression options")
                self.compressor = "compress"
            if o in ('-I', '--bzip2'):
                if (self.compressor):
                    user_error("conflicting compression options")
                self.compressor = "bzip2"
        if not self.compressor:
            self.compressor = "gzip"
        extensions = {
            "compress" : ".Z",
            "gzip"     : ".gz",
            "bzip2"    : ".bz2",
            }
        self.compressor_extension = extensions[self.compressor]
        return args


class Mbox(mailbox.PortableUnixMailbox):
    """Class that allows read/write access to a 'mbox' mailbox. 
    Subclasses the mailbox.PortableUnixMailbox class.
    """
   
    mbox_file = None   # file handle for the mbox file

    def __init__(self, path_name):
        """Constructor for opening an existing 'mbox' mailbox.
        Extends constructor for mailbox.PortableUnixMailbox()

        Arguments:
        path_name -- file name of the 'mbox' file to be opened

        """
        assert(path_name)
        try:
            self.mbox_file = open(path_name, "r")
        except IOError, msg:
            unexpected_error(msg)
        mailbox.PortableUnixMailbox.__init__(self, self.mbox_file)

    def write(self, msg):
        """Write a rfc822 message object to the 'mbox' mailbox.
        If the rfc822 has no Unix 'From_' line, then one is constructed
        from other headers in the message.

        Arguments:
        msg -- rfc822 message object to be written

        """
        assert(msg)
        vprint("saving message to file '%s'" % self.mbox_file.name)
        unix_from = msg.unixfrom
        if not unix_from:
            unix_from = make_mbox_from(msg)
        self.mbox_file.write(unix_from)
        assert(msg.headers)
        self.mbox_file.writelines(msg.headers)
        self.mbox_file.write(os.linesep)

        # The following while loop is about twice as fast in 
        # practice to 'self.mbox_file.writelines(msg.fp.readlines())'
        while 1:
            body = msg.fp.read(8192)
            if not body:
                break
            self.mbox_file.write(body)

    def remove(self):
        """Close and delete the 'mbox' mailbox file"""
        file_name = self.mbox_file.name
        self.close()
        vprint("removing file '%s'" % self.mbox_file.name)
        os.remove(file_name)

    def is_empty(self):
        """Return true if the 'mbox' file is empty, false otherwise"""
        return (os.path.getsize(self.mbox_file.name) == 0)

    def close(self):
        """Close the mbox file"""
        if not self.mbox_file.closed:
            vprint("closing file '%s'" % self.mbox_file.name)
            self.mbox_file.close()

    def exclusive_lock(self):
        """Set an advisory lock on the 'mbox' mailbox"""
        vprint("obtaining exclusive lock on file '%s'" % self.mbox_file.name)
        fcntl.flock(self.mbox_file, fcntl.LOCK_EX)

    def exclusive_unlock(self):
        """Unset any advisory lock on the 'mbox' mailbox"""
        vprint("dropping exclusive lock on file '%s'" % self.mbox_file.name)
        fcntl.flock(self.mbox_file, fcntl.LOCK_UN)

    def procmail_lock(self):
        """Create a procmail lockfile on the 'mbox' mailbox"""
        lock_name = self.mbox_file.name + _options.lockfile_extension
        attempt = 0
        while os.path.isfile(lock_name):
            vprint("lockfile '%s' exists - sleeping..." % lock_name)
            time.sleep(_options.lockfile_sleep)
            attempt = attempt + 1
            if (attempt >= _options.lockfile_attempts):
                unexpected_error("Giving up waiting for procmail lock '%s'" 
                    % lock_name)
        vprint("writing lockfile '%s'" % lock_name)
        lock = open(lock_name, "w")
        _stale.procmail_lock = lock_name
        lock.close()

    def procmail_unlock(self):
        """Delete the procmail lockfile on the 'mbox' mailbox"""
        assert(self.mbox_file.name)
        lock_name = self.mbox_file.name + _options.lockfile_extension
        vprint("removing lockfile '%s'" % lock_name)
        os.remove(lock_name)
        _stale.procmail_lock = None

    def leave_empty(self):
        """Replace the 'mbox' mailbox with a zero-length file.
        This should be the same as 'cp /dev/null mailbox'.
        This will leave a zero-length mailbox file so that mail
        reading programs don't get upset that the mailbox has been
        completely deleted."""
        assert(os.path.isfile(self.mbox_file.name))
        vprint("turning '%s' into a zero-length file" % self.mbox_file.name)
        atime = os.path.getatime(self.mbox_file.name)
        mtime = os.path.getmtime(self.mbox_file.name)
        blank_file = open(self.mbox_file.name, "w")
        blank_file.close()
        os.utime(self.mbox_file.name, (atime, mtime)) # to original timestamps


class RetainMbox(Mbox):
    """Class for holding messages that will be retained from the original
    mailbox (ie. the messages are not considered 'old'). Extends the 'Mbox'
    class. This 'mbox' file starts off as a temporary file but will eventually
    overwrite the original mailbox if everything is OK. 
    
    """
    __final_name = None

    def __init__(self, final_name):
        """Constructor - create a temporary file for the mailbox.
       
        Arguments:
        final_name -- the name of the original mailbox that this mailbox
                      will replace when we call finalise()

        """
        assert(final_name)
        temp_name = tempfile.mktemp("archivemail_retain")
        self.mbox_file = open(temp_name, "w")
        _stale.retain = temp_name
        vprint("opened temporary retain file '%s'" % self.mbox_file.name)
        self.__final_name = final_name

    def finalise(self):
        """Overwrite the original mailbox with this temporary mailbox."""
        assert(self.__final_name)
        self.close()
        atime = os.path.getatime(self.__final_name)
        mtime = os.path.getmtime(self.__final_name)
        vprint("renaming '%s' to '%s'" % (self.mbox_file.name, self.__final_name))
        os.rename(self.mbox_file.name, self.__final_name)
        os.utime(self.__final_name, (atime, mtime)) # reset to original timestamps
        _stale.retain = None

    def remove(self):
        """Delete this temporary mailbox. Overrides Mbox.remove()"""
        Mbox.remove(self)
        _stale.retain = None


class ArchiveMbox(Mbox):
    """Class for holding messages that will be archived from the original
    mailbox (ie. the messages that are considered 'old'). Extends the 'Mbox'
    class. This 'mbox' file starts off as a temporary file, extracted from any
    pre-existing archive. It will eventually overwrite the original archive
    mailbox if everything is OK. 
    
    """
    __final_name = None 

    def __init__(self, final_name):
        """Constructor -- extract any pre-existing compressed archive to a
        temporary file which we use as the new 'mbox' archive for this
        mailbox. 
       
        Arguments:
        final_name -- the final name for this archive mailbox. This function
                      will check to see if the filename already exists, and
                      extract it to a temporary file if it does. It will also
                      rename itself to this name when we call finalise()

        """
        assert(final_name)
        compressor = _options.compressor
        compressedfilename = final_name + _options.compressor_extension
       
        if os.path.isfile(final_name):
            unexpected_error("""There is already a file named '%s'!
Have you been reading this archive? You probably should re-compress it
manually, and try running me again.""" % final_name)

        temp_name = tempfile.mktemp("archivemail_archive")

        if os.path.isfile(compressedfilename):
            vprint("file already exists that is named: %s" % compressedfilename)
            uncompress =  "%s -d -c %s > %s" % (compressor, 
                compressedfilename, temp_name)
            vprint("running uncompressor: %s" % uncompress)
            _stale.archive = temp_name
            system_or_die(uncompress)

        _stale.archive = temp_name
        self.mbox_file = open(temp_name, "a")
        self.__final_name = final_name

    def finalise(self):
        """Compress the archive and rename this archive temporary file to the
        final archive filename, overwriting any pre-existing archive if it
        exists.

        """
        assert(self.__final_name)
        self.close()
        compressor = _options.compressor
        compressed_archive_name = self.mbox_file.name +  \
            _options.compressor_extension
        compress = compressor + " " + self.mbox_file.name
        vprint("running compressor: '%s'" % compress)
        _stale.compressed_archive = compressed_archive_name
        system_or_die(compress)
        _stale.archive = None
        compressed_final_name = self.__final_name + _options.compressor_extension
        vprint("renaming '%s' to '%s'" % (compressed_archive_name, 
            compressed_final_name))
        os.rename(compressed_archive_name, compressed_final_name)
        _stale.compressed_archive = None


class IdentityCache:
    seen_ids = {}
    mailbox_name = None

    def __init__(self, mailbox_name):
        assert(mailbox_name)
        self.mailbox_name = mailbox_name

    def warn_if_dupe(self, msg):
        assert(msg)
        message_id = msg.get('Message-ID')
        assert(message_id)
        if self.seen_ids.has_key(message_id):
            user_warning("duplicate message id: '%s' in mailbox '%s'" % 
                (message_id, self.mailbox_name))
        self.seen_ids[message_id] = 1


# global class instances
_options = Options()  # the run-time options object


def main(args = sys.argv[1:]):
    global _stale

    usage = """Usage: %s [options] mailbox [mailbox...]
Moves old mail messages in mbox or maildir-format mailboxes to compressed
'mbox' mailbox archives. This is useful for saving space and keeping your
mailbox manageable.

Options are as follows:
  -d, --days=<days>    archive messages older than <days> days (default: %d)
  -o, --output-dir=DIR directory where archive files go (default: current)
  -s, --suffix=NAME    suffix for archive filename (default: '%s')
  -n, --dry-run        don't write to anything - just show what would be done
  -z, --gzip           compress the archive(s) using gzip (default) 
  -I, --bzip2          compress the archive(s) using bzip2
  -Z, --compress       compress the archive(s) using compress
      --delete         delete rather than archive old mail (use with caution!)
      --warn-duplicate warn about duplicate Message-IDs in the same mailbox
  -m, --modify-time    use file last-modified time as date for maildir messages
  -v, --verbose        report lots of extra debugging information
  -q, --quiet          quiet mode - print no statistics (suitable for crontab)
  -V, --version        display version information
  -h, --help           display this message

Example: %s linux-devel
  This will move all messages older than %s days to a 'mbox' mailbox called 
  'linux-devel_archive.gz', deleting them from the original 'linux-devel'
  mailbox. If the 'linux-devel_archive.gz' mailbox already exists, the 
  newly archived messages are appended.

Website: http://archivemail.sourceforge.net/ """ %   \
    (_options.script_name, _options.days_old_max, _options.archive_suffix,
    _options.script_name, _options.days_old_max)

    args = _options.parse_args(args, usage)
    if len(args) == 0:
        print usage
        sys.exit(1)

    os.umask(077) # saves setting permissions on mailboxes/tempfiles

    # Make sure we clean up nicely - we don't want to leave stale procmail
    # lockfiles about if something bad happens to us. This is quite 
    # important, even though procmail will delete stale files after a while.
    _stale = StaleFiles() # remember what we have to delete
    atexit.register(clean_up) # delete stale files on exceptions/normal exit
    signal.signal(signal.SIGHUP, clean_up_signal)   # signal 1
    # SIGINT (signal 2) is handled as a python exception
    signal.signal(signal.SIGQUIT, clean_up_signal)  # signal 3
    signal.signal(signal.SIGTERM, clean_up_signal)  # signal 15

    for mailbox_path in args:
        archive(mailbox_path)


######## errors and debug ##########

def vprint(string):
    """Print the string argument if we are in verbose mode"""
    if _options.verbose:
        print string


def unexpected_error(string):
    """Print the string argument, a 'shutting down' message and abort - 
    this function never returns"""
    sys.stderr.write("%s: %s\n" % (_options.script_name, string))
    sys.stderr.write("%s: unexpected error encountered - shutting down\n" % 
        _options.script_name)
    sys.exit(1)


def user_error(string):
    """Print the string argument and abort - this function never returns"""
    sys.stderr.write("%s: %s\n" % (_options.script_name, string))
    sys.exit(1)


def user_warning(string):
    """Print the string argument"""
    sys.stderr.write("%s: Warning - %s\n" % (_options.script_name, string))

########### operations on a message ############

def make_mbox_from(message):
    """Return a string suitable for use as a 'From_' mbox header for the
    message.

    Arguments:
    message -- the rfc822 message object

    """
    assert(message)
    address_header = message.get('Return-path')
    if not address_header:
        vprint("make_mbox_from: no Return-path -- using 'From:' instead!")
        address_header = message.get('From')
    (name, address) = rfc822.parseaddr(address_header)
    date = rfc822.parsedate(message.get('Delivery-date'))
    if not date:
        date = rfc822.parsedate(message.get('Date'))
    date_string = time.asctime(date)
    mbox_from = "From %s %s\n" % (address, date_string)
    return mbox_from

def get_date_mtime(message):
    """Return the delivery date of an rfc822 message in a maildir mailbox""" 
    assert(message)
    vprint("using last-modification time of message file")
    return os.path.getmtime(message.fp.name)

def get_date_headers(message):
    """Return the delivery date of an rfc822 message in a mbox mailbox""" 
    assert(message)
    date = message.getdate('Date')
    delivery_date = message.getdate('Delivery-date')
    use_date = None
    time_message = None
    if delivery_date:
        try:
            time_message = time.mktime(delivery_date)
            use_date = delivery_date
            vprint("using message 'Delivery-date' header")
        except ValueError:
            pass
    if date and not use_date:
        try:
            time_message = time.mktime(date)
            use_date = date
            vprint("using message 'Date' header")
        except ValueError:
            pass
    if not use_date:
        unexpected_error("no valid dates found for message")
    return time_message
       

def is_too_old(time_message):
    """Return true if a message is too old (and should be archived), 
    false otherwise.

    Arguments:
    time_message -- the delivery date of the message measured in seconds
                    since the epoch
       
    """
    assert(time_message)
    time_now = time.time()
    if time_message > time_now:
        vprint("warning: message has date in the future")
        return 0
    secs_old_max = (_options.days_old_max * 24 * 60 * 60)
    days_old = (time_now - time_message) / 24 / 60 / 60
    vprint("message is %.2f days old" % days_old)
    if ((time_message + secs_old_max) < time_now):
        return 1
    return 0


###############  mailbox operations ###############

def archive(mailbox_name):
    """Archives a mailbox.

    Arguments:
    mailbox_name -- the filename/dirname of the mailbox to be archived
    final_archive_name -- the filename of the 'mbox' mailbox to archive
                          old messages to - appending if the archive 
                          already exists

    """
    assert(mailbox_name)

    tempfile.tempdir = choose_temp_dir(mailbox_name)
    vprint("set tempfile directory to '%s'" % tempfile.tempdir)

    final_archive_name = mailbox_name + _options.archive_suffix
    if _options.output_dir:
        final_archive_name = os.path.join(_options.output_dir, 
            os.path.basename(final_archive_name))
    vprint("archiving '%s' to '%s' ..." % (mailbox_name, final_archive_name))

    if os.path.islink(mailbox_name):
        unexpected_error("'%s' is a symbolic link -- I am nervous" % 
            mailbox_name)
    elif os.path.isfile(mailbox_name):
        vprint("guessing mailbox is of type: mbox")
        _archive_mbox(mailbox_name, final_archive_name)
    elif os.path.isdir(mailbox_name):
        cur_path = os.path.join(mailbox_name, "cur")
        new_path = os.path.join(mailbox_name, "new")
        if os.path.isdir(cur_path) and os.path.isdir(new_path):
            vprint("guessing mailbox is of type: maildir")
            _archive_dir(mailbox_name, final_archive_name, "maildir")
        else:
            vprint("guessing mailbox is of type: MH")
            _archive_dir(mailbox_name, final_archive_name, "mh")
    else:
        user_error("'%s': no such file or directory" % mailbox_name)


def _archive_mbox(mailbox_name, final_archive_name):
    """Archive a 'mbox' style mailbox - used by archive_mailbox()

    Arguments:
    mailbox_name -- the filename/dirname of the mailbox to be archived
    final_archive_name -- the filename of the 'mbox' mailbox to archive
                          old messages to - appending if the archive 
                          already exists
    """
    assert(mailbox_name)
    assert(final_archive_name)

    archive = None
    retain = None
    stats = Stats(mailbox_name, final_archive_name)
    original = Mbox(mailbox_name)
    cache = IdentityCache(mailbox_name)

    original.procmail_lock()
    original.exclusive_lock()
    msg = original.next()
    while (msg):
        stats.another_message()
        vprint("processing message '%s'" % msg.get('Message-ID'))
        if _options.warn_duplicates:
            cache.warn_if_dupe(msg)             
        time_message = get_date_headers(msg)
        if is_too_old(time_message):
            stats.another_archived()
            if _options.delete_old_mail:
                vprint("decision: delete message")
            else:
                vprint("decision: archive message")
                if not _options.dry_run:
                    if (not archive):
                        archive = ArchiveMbox(final_archive_name)
                    archive.write(msg)
        else:
            vprint("decision: retain message")
            if not _options.dry_run:
                if (not retain):
                    retain = RetainMbox(mailbox_name)
                retain.write(msg)
        msg = original.next()
    vprint("finished reading messages") 
    original.exclusive_unlock()
    original.close()
    if not _options.dry_run:
        if retain: retain.close()
        if archive: archive.close()
        if _options.delete_old_mail:
            # we will never have an archive file
            if retain:
                retain.finalise(mailbox_name)
            else:
                # nothing was retained - everything was deleted
                original.leave_empty()
        elif archive:
            archive.finalise()
            if retain:
                retain.finalise()
            else:
                # nothing was retained - everything was deleted
                original.leave_empty()
        else:
            # There was nothing to archive
            if retain:
                # retain will be the same as original mailbox 
                retain.remove()
    original.procmail_unlock()
    if not _options.quiet:
        stats.display()


def _archive_dir(mailbox_name, final_archive_name, type):
    """Archive a 'maildir' or 'MH' style mailbox - used by archive_mailbox()"""
    assert(mailbox_name)
    assert(final_archive_name)
    assert(type)
    original = None
    archive = None
    stats = Stats(mailbox_name, final_archive_name)
    delete_queue = []

    if type == "maildir":
        original = mailbox.Maildir(mailbox_name)
    elif type == "mh":
        original = mailbox.MHMailbox(mailbox_name)
    else:
        unexpected_error("unknown type: %s" % type)        
    assert(original)

    cache = IdentityCache(mailbox_name)

    msg = original.next()
    while (msg):
        stats.another_message()
        vprint("processing message '%s'" % msg.get('Message-ID'))
        if _options.warn_duplicates:
            cache.warn_if_dupe(msg)             
        if _options.use_modify_time:
            time_message = get_date_mtime(msg)
        else:
            time_message = get_date_headers(msg)
        if is_too_old(time_message):
            stats.another_archived()
            if _options.delete_old_mail:
                vprint("decision: delete message")
            else:
                vprint("decision: archive message")
                if not _options.dry_run:
                    if (not archive):
                        archive = ArchiveMbox(final_archive_name)
                    archive.write(msg)
            if not _options.dry_run: delete_queue.append(msg.fp.name) 
        else:
            vprint("decision: retain message")
        msg = original.next()
    vprint("finished reading messages") 
    if not _options.dry_run:
        if archive:
            archive.close()
            archive.finalise()
        for file_name in delete_queue:
            if os.path.isfile(file_name):
                vprint("removing original message: '%s'" % file_name)
                os.remove(file_name)
    if not _options.quiet:
        stats.display()


###############  misc  functions  ###############

def clean_up():
    """Delete stale files -- to be registered with atexit.register()"""
    vprint("cleaning up ...")
    _stale.clean()


def clean_up_signal(signal_number, stack_frame):
    """Delete stale files -- to be registered as a signal handler.

    Arguments:
    signal_number -- signal number of the terminating signal
    stack_frame -- the current stack frame
    
    """
    # this will run the above clean_up(), since unexpected_error()
    # will abort with sys.exit() and clean_up will be registered 
    # at this stage
    unexpected_error("received signal %s" % signal_number)


def choose_temp_dir(mailbox_path):
    """Set the directory for temporary files to something safe.
    
    Arguments:
    mailbox_path -- path name to the original mailbox

    """
    assert(mailbox_path)
    temp_dir = os.path.dirname(mailbox_path)
    if _options.output_dir:
        temp_dir = _options.output_dir
    if not temp_dir:
        temp_dir = os.curdir # use the current directory
    return temp_dir

def system_or_die(command):
    """Run the command with os.system(), aborting on non-zero exit"""
    assert(command)
    rv = os.system(command)
    if (rv != 0):
        status = os.WEXITSTATUS(rv)
        unexpected_error("command '%s' returned status %d" % (command, status))


# this is where it all happens, folks
if __name__ == '__main__':
    main()
