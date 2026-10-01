import logging
import zipfile
import os


logger = logging.getLogger(__name__)

class DatasetProvider:
    def __init__(self, cache_dir="~/.cache_raw_spider"):
        self.raw_data_dir = os.path.expanduser(cache_dir)
        self.zip_path = os.path.join(self.raw_data_dir, "spider_raw")
        self.extracted_path = os.path.join(self.raw_data_dir, "spider")
        self.file_id = "1TqleXec_OykOYFREKKtschzY29dUcVAQ"

    def _download(self):
        import gdown

        if os.path.exists(self.zip_path):
            logger.info("Dataset already downloaded")
            return

        os.makedirs(self.raw_data_dir, exist_ok=True)
        logger.info("Downloading raw data")
        gdown.download(id=self.file_id, output=self.zip_path, quiet=False)

    def _extract(self):
        if os.path.exists(self.extracted_path):
            logger.info("Dataset already extracted")
            return

        logger.info("Extracting raw data")
        with zipfile.ZipFile(self.zip_path, 'r') as zip:
            zip.extractall(self.raw_data_dir)
        logger.info("Extracted raw data")

    def ensure_data_ready(self):
        self._download()
        self._extract()
        return self.extracted_path

    def get_sql_path(self, db_id):
        return os.path.join(self.extracted_path, "database", db_id, f"{db_id}.sqlite")
