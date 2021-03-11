import traceback
import sys


def error_message(e, message=None, cause=None):
    """
    Formats exception message + cause
    :param e:
    :param message:
    :param cause:
    :return: formatted message, includes cause if any is set
    """
    if message is None and cause is None:
        return None
    elif message is None:
        return '%s, caused by %r' % (e.__class__, cause)
    elif cause is None:
        return message
    else:
        return '%s, caused by %r' % (message, cause)


class Error(Exception):
    """Generic EB client error."""
    def __init__(self, message=None, cause=None, do_message=True):
        super(Error, self).__init__(error_message(self, message, cause))
        self.cause = cause
        self.message = message
        self.base_message = message

        self.exc_type, self.exc_value, self.exc_traceback = None, None, None
        self.traceback_formatted = None
        self.traceback = None

        self.load(cause, do_message=do_message)

    def load(self, cause=None, do_message=True):
        """
        Loads exception data from the current exception frame - should be called inside the except block
        :return:
        """
        if cause is not None:
            self.cause = cause
            if do_message:
                self.message = error_message(self, self.base_message, cause)

        self.exc_type, self.exc_value, self.exc_traceback = sys.exc_info()
        self.traceback_formatted = traceback.format_exc()
        self.traceback = traceback.extract_tb(self.exc_traceback)
        return self
