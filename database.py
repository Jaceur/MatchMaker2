"""Connection layer: the one engine + MetaData the whole app shares.

Every other module imports `engine` and `metadata` from here, so there is a
single connection pool and a single schema registry in play.
"""
import streamlit as st
from google.oauth2 import service_account
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, MetaData


# Wrapping this in Streamlit's cache prevents duplicate connections when the
# engine is imported across the app's pages.
@st.cache_resource
def get_backend_engine():
    # 1. Read the passport from Streamlit Secrets
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"]
    )

    # 2. Hand the passport to the Connector
    connector = Connector(credentials=creds)

    def getconn():
        return connector.connect(
            "enrichmentno:europe-west2:matchmaker-2",
            "pg8000",
            user="postgres",
            password=st.secrets["DB_PASSWORD"],
            db="sales-pipeline",
            ip_type=IPTypes.PUBLIC
        )
    return create_engine("postgresql+pg8000://", creator=getconn, pool_pre_ping=True)


# The shared singletons. Import these — never build your own.
engine = get_backend_engine()
metadata = MetaData()
