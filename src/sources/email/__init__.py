"""E-Mail als Source Connector (Projektbenachrichtigungen der Portale)."""

from src.sources.email.connector import EmailSourceConnector
from src.sources.email.mailbox import EmlDirectoryMailbox, InMemoryMailbox, Mailbox, MailboxMessage
from src.sources.email.gmail_mailbox import GmailMailbox
from src.sources.email.seen_store import InMemorySeenMessageStore, ProcessedMessage, SeenMessageStore

__all__ = [
    "EmailSourceConnector",
    "EmlDirectoryMailbox",
    "GmailMailbox",
    "InMemoryMailbox",
    "InMemorySeenMessageStore",
    "Mailbox",
    "MailboxMessage",
    "ProcessedMessage",
    "SeenMessageStore",
]
