#importando bibliotecas
import pandas as pd
import requests
import sqlite3
from datetime import datetime
import sys

#var globais
db_server = 'clima.db'
table_name = 'clima'
csv_path = 'clima.csv'
log_file = 'code_log.txt'

#cidades e data:
cidades = {'Brasilia': {'latitude': '-15.79', 'longitude': '-47.88'},
           'Goiania': {'latitude': '-16.68', 'longitude': '-49.25'},
           'Sao Paulo': {'latitude': '-23.55', 'longitude': '-46.63'},
           'Joao Pessoa': {'latitude': '-7.11', 'longitude': '-34.88'},
           'Dublin': {'latitude': '53.34', 'longitude': '-6.26'}}
data_start = '2026-09-01'
data_end = '2026-09-30'
           

#etl_clima
def log_progress(message):
    timestamp_format = '%Y-%m-%d-%H:%M:%S'
    now = datetime.now()
    timestamp = now.strftime(timestamp_format)
    with open(log_file, 'a') as f:
        f.write(timestamp + ' : ' + message + '\n')

def extract(cidades_dict, start, end):
    df = pd.DataFrame(columns=['cidade', 'data', 'temp_max', 'temp_min', 'precipitacao_mm'])
    for i, j in cidades_dict.items():
        latitude = j['latitude']
        longitude = j['longitude']
        url = f'https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start}&end_date={end}&daily=precipitation_sum,temperature_2m_max,temperature_2m_min&timezone=auto'

        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            
            dados = r.json()
            daily = dados['daily']

            dados_dict = {'cidade': i,
                        'data': daily['time'],
                        'temp_max': daily['temperature_2m_max'],
                        'temp_min': daily['temperature_2m_min'],
                        'precipitacao_mm': daily['precipitation_sum']}

            df1 = pd.DataFrame(dados_dict)
            df = pd.concat([df, df1], ignore_index=True)

            log_progress(f'Dados extraidos: {i}')
            print(f'Dados extraídos: {i}')
            
        except requests.exceptions.Timeout:
            log_progress(f'Timeout ao consultar: {i}')
        except requests.exceptions.HTTPError as e:
            log_progress(f'Erro HTTP em {i}: {e}')
        except requests.exceptions.ConnectionError:
            log_progress(f'Não foi possivel conectar {i}.')
        except requests.exceptions.JSONDecodeError:
            log_progress(f'JSON invalido ao consultar {i}')
        except (KeyError, TypeError, ValueError) as e:
            log_progress(f'Erro na estrutura de dados de {i}: {e}')
        except requests.exceptions.RequestException as e:
            log_progress(f'Erro na requisição: {e}')

    if df.empty:
        raise ValueError('DataFrame vazio, Não foi possível continuar!')
    else:
        return df


def transform(df):
    
    #converte a data[str] para datetime
    df['data'] = pd.to_datetime(df['data'], format='%Y-%m-%d')

    #amplitude_termica é a temp_max - temp_min de cada dia
    df['amplitude_termica'] = df['temp_max'] - df['temp_min']

    #verifica se a precipitacao é maior que 0 e coloca valor 1 ou 0 na coluna 'choveu'
    df['choveu'] = (df['precipitacao_mm'] > 0).astype(int)
    
    #cria media movel de 7 dias com groupby para cada cidade usando valor temp_max
    df = df.sort_values(['cidade', 'data'])
    df['media_movel_7'] = df.groupby('cidade')['temp_max'].rolling(window=7).mean().reset_index(level=0, drop=True)
    
    return df

def create_table(sql_connection, table_name):
    cursor = sql_connection.cursor()

    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS {table_name} (
    cidade TEXT,
    data TEXT,
    temp_max REAL,
    temp_min REAL,
    precipitacao_mm REAL,
    amplitude_termica REAL,
    choveu INTEGER,
    media_movel_7 REAL,
    PRIMARY KEY (cidade, data)
    )
    ''')

    sql_connection.commit()


def load_to_csv(df, csv_path):
    try:
        df_original = pd.read_csv(csv_path)
        df_original['data'] = pd.to_datetime(df_original['data'], format='%Y-%m-%d')
    except FileNotFoundError:
        df_original = pd.DataFrame()

    df_novos = df

    df_concat = pd.concat([df_original, df_novos], ignore_index=True)
    df_concat = df_concat.sort_values(['cidade', 'data'])
    df_final = df_concat.drop_duplicates(subset=['cidade', 'data'], keep='first')
    df_final.to_csv(csv_path, index=False)

def load_to_db(df, sql_connection, table_name):
    cursor = sql_connection.cursor()

    insert = f'''
    INSERT INTO {table_name}
    (cidade, data, temp_max, temp_min, precipitacao_mm, amplitude_termica, choveu, media_movel_7)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(cidade, data) DO NOTHING
    '''

    df = df.copy()
    df['data'] = df['data'].dt.strftime('%Y-%m-%d')

    dados = df[['cidade', 'data', 'temp_max', 'temp_min', 'precipitacao_mm', 'amplitude_termica', 'choveu', 'media_movel_7']].itertuples(index=False, name=None)

    cursor.executemany(insert, dados)
    sql_connection.commit()


def main():
    #pipeline:
    sql_connection = None
    try:
        log_progress('iniciando ETL')
        
        extraction = extract(cidades, data_start, data_end)
        log_progress('Extração concluida!')
        
        transformation = transform(extraction)
        log_progress('Transformação concluida!')
        
        load_to_csv(transformation, csv_path)
        log_progress('DataFrame convertido para CSV')

        sql_connection = sqlite3.connect(db_server)
        log_progress('SQL conectado com sucesso!')

        create_table(sql_connection, table_name)
        log_progress('Tabela pronta para inserir dados!')
        
        load_to_db(transformation, sql_connection, table_name)
        log_progress('load para sql concluido com sucesso!')


    except Exception as e:
        log_progress(f'Pipeline interrompido: {e}')
        raise
    finally:
        if sql_connection:
            sql_connection.close()
            log_progress('SQL connection fechada com sucesso!')

if __name__ == '__main__':
    main()