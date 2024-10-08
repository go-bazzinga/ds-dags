from airflow import DAG
from airflow.utils.dates import days_ago
from airflow.operators.python_operator import PythonOperator
from google.cloud import bigquery
import requests

def send_alert_to_google_chat():
    webhook_url = "https://chat.googleapis.com/v1/spaces/AAAAkUFdZaw/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=VC5HDNQgqVLbhRVQYisn_IO2WUAvrDeRV9_FTizccic"
    message = {
        "text": f"DAG user_video_relation failed."
    }
    requests.post(webhook_url, json=message)

def check_table_exists():
    client = bigquery.Client()
    query = """
    SELECT COUNT(*)
    FROM `hot-or-not-feed-intelligence.yral_ds.INFORMATION_SCHEMA.TABLES`
    WHERE table_name = 'userVideoRelation'
    """
    query_job = client.query(query)
    results = query_job.result()
    for row in results:
        return row[0] > 0

def get_last_timestamp():
    client = bigquery.Client()
    query = """
    SELECT MAX(last_watched_timestamp) as last_watched_timestamp
    FROM `hot-or-not-feed-intelligence.yral_ds.userVideoRelation`
    """
    query_job = client.query(query)
    results = query_job.result()
    for row in results:
        return row['last_watched_timestamp']

def create_initial_query():
    return """
    CREATE OR REPLACE TABLE `hot-or-not-feed-intelligence.yral_ds.userVideoRelation` AS
    WITH video_watched AS (
      SELECT 
        JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
        JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
        max(timestamp) as last_watched_timestamp,
        AVG(CAST(JSON_EXTRACT_SCALAR(params, '$.percentage_watched') AS FLOAT64))/100 AS mean_percentage_watched
      FROM 
        analytics_335143420.test_events_analytics -- base analytics table -- change this if the table name changes
      WHERE 
        event = 'video_duration_watched'
        AND CAST(JSON_EXTRACT_SCALAR(params, '$.percentage_watched') AS FLOAT64) <= 100 -- there is some issue if this is greater than 100
      GROUP BY 
        user_id, video_id
    ),
    video_liked AS (
      SELECT 
        JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
        JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
        max(timestamp) as last_liked_timestamp,
        TRUE AS liked
      FROM 
        analytics_335143420.test_events_analytics
      WHERE 
        event = 'like_video'
      GROUP BY 
        user_id, video_id
    ),
    video_shared as (
      SELECT
        JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
        JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
        max(timestamp) as last_shared_timestamp,
        TRUE AS shared
      FROM
        analytics_335143420.test_events_analytics
      WHERE
        event = 'share_video'
      GROUP BY
        user_id, video_id
    )
    SELECT 
      vw.user_id,
      vw.video_id,
      vw.last_watched_timestamp,
      vw.mean_percentage_watched,
      vl.last_liked_timestamp,
      COALESCE(vl.liked, FALSE) AS liked,
      vs.last_shared_timestamp,
      COALESCE(vs.shared, FALSE) AS shared
    FROM 
      video_watched vw
    LEFT JOIN 
      video_liked vl
    ON 
        vw.user_id = vl.user_id 
        AND vw.video_id = vl.video_id
    LEFT JOIN
      video_shared vs
    ON
      vw.user_id = vs.user_id
      AND vw.video_id = vs.video_id
    order by last_watched_timestamp desc; -- unit tests -- per video id & per user id
    """

def create_incremental_query(last_timestamp):
    return f"""
    MERGE `hot-or-not-feed-intelligence.yral_ds.userVideoRelation` T
    USING (
      WITH video_watched AS (
        SELECT 
          JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
          JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
          max(timestamp) as last_watched_timestamp,
          AVG(CAST(JSON_EXTRACT_SCALAR(params, '$.percentage_watched') AS FLOAT64))/100 AS mean_percentage_watched
        FROM 
          analytics_335143420.test_events_analytics
        WHERE 
          event = 'video_duration_watched'
          AND timestamp > '{last_timestamp}'
          AND CAST(JSON_EXTRACT_SCALAR(params, '$.percentage_watched') AS FLOAT64) <= 100 -- there is some issue if this is greater than 100
        GROUP BY
          user_id, video_id
      ),
      video_liked AS (
        SELECT 
          JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
          JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
          max(timestamp) as last_liked_timestamp,
          TRUE AS liked
        FROM 
          analytics_335143420.test_events_analytics
        WHERE 
          event = 'like_video'
          and timestamp > '{last_timestamp}'
        GROUP BY 
          user_id, video_id
      ),
      video_shared as (
        SELECT
          JSON_EXTRACT_SCALAR(params, '$.user_id') AS user_id,
          JSON_EXTRACT_SCALAR(params, '$.video_id') AS video_id,
          max(timestamp) as last_shared_timestamp,
          TRUE AS shared
        FROM
          analytics_335143420.test_events_analytics
        WHERE
          event = 'share_video'
          and timestamp > '{last_timestamp}'
        GROUP BY
          user_id, video_id
      )
      SELECT 
        vw.user_id,
        vw.video_id,
        vw.last_watched_timestamp,
        vw.mean_percentage_watched,
        vl.last_liked_timestamp,
        COALESCE(vl.liked, FALSE) AS liked,
        vs.last_shared_timestamp,
        COALESCE(vs.shared, FALSE) AS shared
      FROM 
        video_watched vw
      LEFT JOIN 
        video_liked vl
      ON 
        vw.user_id = vl.user_id AND vw.video_id = vl.video_id
      LEFT JOIN
        video_shared vs
      ON
        vw.user_id = vs.user_id
        AND vw.video_id = vs.video_id
      ORDER BY 
        vw.last_watched_timestamp DESC
    ) S
    ON T.user_id = S.user_id AND T.video_id = S.video_id
    WHEN MATCHED THEN
      UPDATE SET 
        T.mean_percentage_watched = S.mean_percentage_watched,
        T.last_watched_timestamp = S.last_watched_timestamp,
        T.last_liked_timestamp = S.last_liked_timestamp,
        T.liked = T.liked OR S.liked,
        T.last_shared_timestamp = S.last_shared_timestamp,
        T.shared = T.shared OR S.shared
    WHEN NOT MATCHED THEN
      INSERT (user_id, video_id, last_watched_timestamp, mean_percentage_watched, last_liked_timestamp, liked, last_shared_timestamp, shared)
      VALUES (S.user_id, S.video_id, S.last_watched_timestamp, S.mean_percentage_watched, S.last_liked_timestamp, S.liked, S.last_shared_timestamp, S.shared)
    """

def run_query():
    if check_table_exists():
        last_timestamp = get_last_timestamp()
        query = create_incremental_query(last_timestamp)
    else:
        query = create_initial_query()
    
    client = bigquery.Client()
    query_job = client.query(query)
    query_job.result()

default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
    'retries': 1,
}

with DAG('user_video_interaction_dag', default_args=default_args, schedule_interval='*/15 * * * *', catchup=False) as dag:
    run_query_task = PythonOperator(
        task_id='run_query_task',
        python_callable=run_query,
        on_failure_callback=send_alert_to_google_chat
    )