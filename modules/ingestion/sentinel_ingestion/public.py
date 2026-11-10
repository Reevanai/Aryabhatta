"""Public interface of the ingestion module. Other packages import only from here."""

from sentinel_ingestion.api import router
from sentinel_ingestion.batches import BatchJob, BatchService
from sentinel_ingestion.consumers import IntakeMessage, handle_intake
from sentinel_ingestion.domain.models import DATASETS, Dataset, TransactionInput
from sentinel_ingestion.service import TRANSACTION_TOPIC, Counts, IngestionService

__all__ = [
    "DATASETS",
    "TRANSACTION_TOPIC",
    "BatchJob",
    "BatchService",
    "Counts",
    "Dataset",
    "IngestionService",
    "IntakeMessage",
    "TransactionInput",
    "handle_intake",
    "router",
]
