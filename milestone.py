import wrds
import pandas as pd
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import numpy as np
import statsmodels.api as sm

# Read Data
def read_price_data(conn, country, start_date, end_date, exchg_df):
    exchg_list = exchg_df.loc[exchg_df.Country == country].exchg.tolist()

    price_qry = f"""
    select
      isin,
      datadate,
      conm,
      exchg,
      PRCOD as open_price,
      PRCHD as high_price,
      PRCLD as low_price,
      PRCCD as close_price,
      CSHTRD as trading_volume,
      PRCOD/ajexdi as adj_open_price,
      PRCHD/ajexdi as adj_high_price,
      PRCLD/ajexdi as adj_low_price,
      PRCCD/ajexdi as adj_close_price,
      CSHTRD*ajexdi as adj_trading_volume,
      CSHOC as num_of_listed_stocks,
      PRCCD*CSHOC as market_capitalization,
      DIV as dividend_per_share,
      case
        when prcstd = '10' then 1 else 0
      end trs_flag,
      tpci stock_gb,
      GIND as gic_industry,
      GSUBIND as gic_sub_industry
    from
      comp.g_secd
    where
      datadate > '{start_date}'
      and datadate < '{end_date}'
      and exchg in ({"'" + "','".join(exchg_list) + "'"})
      and isin is not null
    """
    price_data = conn.raw_sql(price_qry)
    
    return price_data


def read_fs_data(conn, country, start_date, end_date, exchg_df):
    exchg_list = exchg_df.loc[exchg_df.Country == country].exchg.tolist()
    
    fs_qry = f"""
    select
      isin,
      exchg,
      fyear,
      datadate,
      AT as assets,
      REVT as revenue,
      OIADP as operating_income,
      EBITDA as earnings_before_interest,
      -- NINC as net_income,
      OANCF as cash_flow
    from (
      select
        *,
        row_number() over(partition by isin, fyear order by datadate) as rn
      from
        comp.g_funda
      where
        datadate > '{start_date}'
        and datadate <= '{end_date}'
        and exchg in ({"'" + "','".join(exchg_list) + "'"})
        and popsrc = 'I'
        and datafmt = 'HIST_STD'
        and consol = 'C'
        and isin is not null
      ) as temp
    where
      rn = 1
    """
    fs_data = conn.raw_sql(fs_qry)
    
    return fs_data


# Create Factors
def create_size_df(price_data):
    size_df = \
        price_data \
        .assign(year = lambda x: x.datadate.astype(str).str[0:4]) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        .sort_values(['exchg','isin','year','datadate']) \
        .groupby(['exchg','isin','year']) \
        .first() \
        .reset_index() \
        .assign(year = lambda x: x.year.astype(int))[['exchg','isin','year','datadate','market_capitalization']]
    return size_df


def create_quality_df(fs_data):
    quality_df = fs_data \
        .rename(columns={'fyear':'year'}) \
        .assign(year = lambda x: x.year+2)[['exchg','isin','year','datadate','assets','revenue','operating_income','earnings_before_interest','cash_flow']]
    return quality_df


def create_value_df(quality_df, size_df):
    value_df = pd.merge(
            quality_df.drop('datadate', axis=1),
            size_df,
            how = 'inner', 
            left_on=['exchg','isin','year'], 
            right_on = ['exchg','isin','year']
        ).assign(
            pbr = lambda x: x.market_capitalization / x.assets,
            psr = lambda x: x.market_capitalization / x.revenue,
            por = lambda x: x.market_capitalization / x.operating_income,
            per = lambda x: x.market_capitalization / x.earnings_before_interest,
            pcr = lambda x: x.market_capitalization / x.cash_flow
        )[['exchg','isin','year','datadate','pbr','psr','por','per','pcr']]
    return value_df


def create_yield_df(price_data):
    yield_df = \
        price_data \
        .assign(year = lambda x: x.datadate.astype(str).str[0:4]) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        .sort_values(['exchg','isin','year','datadate']) \
        .groupby(['exchg','isin','year']) \
        .first() \
        .reset_index() \
        .assign(year = lambda x: x.year.astype(int))[['exchg','isin','year','datadate','dividend_per_share']]
    return yield_df


def create_momentum_df(price_data):
    date_mapping = price_data[['exchg','isin','datadate','adj_close_price']].sort_values(['exchg','isin','datadate','adj_close_price'])
    date_mapping['current_m'] = date_mapping['datadate'].to_numpy().astype('datetime64[M]')
    date_mapping = date_mapping.groupby(['exchg','isin','current_m']).first().reset_index()[['exchg','isin','datadate','current_m']]
    
    date_mapping['delta_1m'] = list(map(lambda x: x+relativedelta(months=1), date_mapping['current_m']))
    date_mapping['delta_6m'] = list(map(lambda x: x+relativedelta(months=6), date_mapping['current_m']))
    date_mapping['delta_12m'] = list(map(lambda x: x+relativedelta(months=12), date_mapping['current_m']))
    date_mapping['delta_24m'] = list(map(lambda x: x+relativedelta(months=24), date_mapping['current_m']))
    date_mapping['delta_36m'] = list(map(lambda x: x+relativedelta(months=36), date_mapping['current_m']))
    date_mapping['delta_minus_12m'] = list(map(lambda x: x+relativedelta(months=-12), date_mapping['current_m']))
    
    date_mapping = date_mapping[['exchg','isin','current_m','datadate']] \
        .merge(date_mapping[['exchg','isin','delta_1m','datadate']].rename(columns={'datadate':'bf_1m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_1m']) \
        .merge(date_mapping[['exchg','isin','delta_6m','datadate']].rename(columns={'datadate':'bf_6m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_6m']) \
        .merge(date_mapping[['exchg','isin','delta_12m','datadate']].rename(columns={'datadate':'bf_12m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_12m']) \
        .merge(date_mapping[['exchg','isin','delta_24m','datadate']].rename(columns={'datadate':'bf_24m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_24m']) \
        .merge(date_mapping[['exchg','isin','delta_36m','datadate']].rename(columns={'datadate':'bf_36m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_36m']) \
        .merge(date_mapping[['exchg','isin','delta_minus_12m','datadate']].rename(columns={'datadate':'aft_12m'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','delta_minus_12m']) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        [['exchg','isin','datadate','bf_1m','bf_6m','bf_12m','bf_24m','bf_36m','aft_12m']]
    
    momentum_df = date_mapping.rename(columns={'datadate':'current_m'}) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'current_price'}), how='inner', left_on=['exchg','isin','current_m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'bf_1m_price'}), how='inner', left_on=['exchg','isin','bf_1m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'bf_6m_price'}), how='inner', left_on=['exchg','isin','bf_6m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'bf_12m_price'}), how='inner', left_on=['exchg','isin','bf_12m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'bf_24m_price'}), how='inner', left_on=['exchg','isin','bf_24m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'bf_36m_price'}), how='inner', left_on=['exchg','isin','bf_36m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1) \
        .merge(price_data[['exchg','isin','datadate','adj_close_price']].rename(columns={'adj_close_price':'aft_12m_price'}), how='inner', left_on=['exchg','isin','aft_12m'], right_on=['exchg','isin','datadate']).drop('datadate', axis=1)
    
    momentum_df = momentum_df \
        .assign(momentum_1m = lambda x: (x.current_price-x.bf_1m_price)/x.bf_1m_price,
                momentum_6m = lambda x: (x.current_price-x.bf_6m_price)/x.bf_6m_price,
                momentum_12m = lambda x: (x.current_price-x.bf_12m_price)/x.bf_12m_price,
                momentum_24m = lambda x: (x.current_price-x.bf_24m_price)/x.bf_24m_price,
                momentum_36m = lambda x: (x.current_price-x.bf_36m_price)/x.bf_36m_price,
                return_1y_later = lambda x: (x.aft_12m_price-x.current_price)/x.current_price) \
        .rename(columns={'current_m':'datadate'}) \
        .assign(year = lambda x: x.datadate.astype(str).str[0:4].astype(int)) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        .sort_values(['exchg','isin','year','datadate']) \
        .groupby(['exchg','isin','year']) \
        .first() \
        .reset_index()[['exchg','isin','year','datadate','momentum_1m','momentum_6m','momentum_12m','momentum_24m','momentum_36m','return_1y_later']]
    
    return momentum_df


def cal_daily_vol(df, month):
    window = 21 * month + 1

    daily_vol = df.groupby(['exchg','isin'])['daily_return'].apply(lambda x: x.rolling(window, min_periods=window).std())

    return daily_vol


def create_volatility_df(price_data):

    volatility_df = price_data[['exchg','isin','datadate','adj_close_price']].sort_values(['exchg','isin','datadate'])
    volatility_df['daily_return'] = volatility_df.groupby(['exchg','isin'])[['adj_close_price']].pct_change()
    
    volatility_df['daily_vol_1m'] = cal_daily_vol(volatility_df, 1)
    volatility_df['daily_vol_3m'] = cal_daily_vol(volatility_df, 3)
    volatility_df['daily_vol_6m'] = cal_daily_vol(volatility_df, 6)
    volatility_df['daily_vol_12m'] = cal_daily_vol(volatility_df, 12)
    volatility_df['daily_vol_24m'] = cal_daily_vol(volatility_df, 24)
    volatility_df['daily_vol_36m'] = cal_daily_vol(volatility_df, 36)
    
    volatility_df = volatility_df \
        .assign(year = lambda x: x.datadate.astype(str).str[0:4].astype(int)) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        .sort_values(['exchg','isin','year','datadate']) \
        .groupby(['exchg','isin','year']) \
        .first() \
        .reset_index()[['exchg','isin','year','datadate','daily_vol_1m','daily_vol_3m','daily_vol_6m','daily_vol_12m','daily_vol_24m','daily_vol_36m']]
    
    return volatility_df


def cal_transaction_amt(df, month):
    window = 21 * month + 1

    daily_vol = df.groupby(['exchg','isin'])['transaction_amount'].apply(lambda x: x.rolling(window, min_periods=1).mean())

    return daily_vol


def create_liquidity_df(price_data):
    liquidity_df = price_data.assign(transaction_amount = lambda x: x.adj_close_price * x.adj_trading_volume)[['exchg','isin','datadate','adj_close_price','transaction_amount']].sort_values(['exchg','isin','datadate'])
    
    liquidity_df['avg_transaction_amount_1m'] = cal_transaction_amt(liquidity_df, 1)
    liquidity_df['avg_transaction_amount_3m'] = cal_transaction_amt(liquidity_df, 3)
    liquidity_df['avg_transaction_amount_6m'] = cal_transaction_amt(liquidity_df, 6)
    liquidity_df['avg_transaction_amount_12m'] = cal_transaction_amt(liquidity_df, 12)
    liquidity_df['avg_transaction_amount_24m'] = cal_transaction_amt(liquidity_df, 24)
    liquidity_df['avg_transaction_amount_36m'] = cal_transaction_amt(liquidity_df, 36)
    
    liquidity_df = liquidity_df \
        .assign(year = lambda x: x.datadate.astype(str).str[0:4].astype(int)) \
        .assign(month = lambda x: x.datadate.astype(str).str[5:7]) \
        .query("month == '01'") \
        .sort_values(['exchg','isin','year','datadate']) \
        .groupby(['exchg','isin','year']) \
        .first() \
        .reset_index() \
        [['exchg','isin','year','datadate',
          'avg_transaction_amount_1m','avg_transaction_amount_3m','avg_transaction_amount_6m',
          'avg_transaction_amount_12m','avg_transaction_amount_24m','avg_transaction_amount_36m']]
    
    return liquidity_df


def create_growth_df(quality_df):
    growth_df = quality_df.sort_values(['exchg','isin','year'])
    
    growth_df['assets_yoy'] = growth_df.groupby(['exchg','isin'])[['assets']].pct_change()
    growth_df['revenue_yoy'] = growth_df.groupby(['exchg','isin'])[['revenue']].pct_change()
    growth_df['operating_income_yoy'] = growth_df.groupby(['exchg','isin'])[['operating_income']].pct_change()
    growth_df['earnings_before_interest_yoy'] = growth_df.groupby(['exchg','isin'])[['earnings_before_interest']].pct_change()
    growth_df['cash_flow_yoy'] = growth_df.groupby(['exchg','isin'])[['cash_flow']].pct_change()
    
    growth_df = growth_df[['exchg','isin','year','datadate','assets_yoy','revenue_yoy','operating_income_yoy','earnings_before_interest_yoy','cash_flow_yoy']]
    
    return growth_df


# Merge Data
def merge_data(size_df, quality_df, value_df, yield_df, volatility_df, liquidity_df, growth_df, momentum_df):
    df = size_df.drop('datadate', axis=1) \
        .merge(quality_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(value_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(yield_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(volatility_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(liquidity_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(growth_df.drop('datadate', axis=1), on=['exchg','isin','year']) \
        .merge(momentum_df.drop('datadate', axis=1), on=['exchg','isin','year'])
    
    return df


# Normalize Data
def normalize_data(df):
    df = \
        pd.concat([
            df[['exchg','isin','year']],
            df.drop('isin',axis=1).groupby(['exchg','year']).transform(lambda x: (x - x.mean()) / x.std())
        ], axis=1)
    return df


# Regression
def factor_significance_check(df):
    na_columns = pd.DataFrame(df.isna().sum()/df.shape[0]).rename(columns={0:'ratio'}).query("ratio >= 0.2").index.tolist()
    
    df_reduced = df.drop(na_columns, axis=1).dropna()
    #print("="*100)
    #print(f"number of rows : {df_reduced.shape[0]}")
    print("="*100)
    y = df_reduced.return_1y_later
    X = df_reduced.drop(['exchg','isin','year','return_1y_later'], axis=1)
    
    fit = sm.OLS(y, X).fit()
    #print(fit.summary())
    
    summary_tbl = \
        pd.merge(
            pd.DataFrame(fit.params).reset_index().rename(columns={'index':'factor',0:'beta'}),
            pd.DataFrame(fit.pvalues).reset_index().rename(columns={'index':'factor',0:'pvalue'}),
            on='factor'
        )
    
    return fit, summary_tbl


# Main Function
def analyze_country(country, start_date, end_date, conn, exchg_df):
    print("Reading Price Data")
    price_data = read_price_data(conn, country, start_date, end_date, exchg_df)
    print("Reading Financial Statement Data")
    fs_data = read_fs_data(conn, country, start_date, end_date, exchg_df)
    
    print("Feature Engineering - Size Factor")
    size_df = create_size_df(price_data)
    print("Feature Engineering - Quality Factor")
    quality_df = create_quality_df(fs_data)
    print("Feature Engineering - Value Factor")
    value_df = create_value_df(quality_df, size_df)
    print("Feature Engineering - Yield Factor")
    yield_df = create_yield_df(price_data)
    print("Feature Engineering - Momentum Factor")
    momentum_df = create_momentum_df(price_data)
    print("Feature Engineering - Volatility Factor")
    volatility_df = create_volatility_df(price_data)
    print("Feature Engineering - Liquidity Factor")
    liquidity_df = create_liquidity_df(price_data)
    print("Feature Engineering - Growth Factor")
    growth_df = create_growth_df(quality_df)
    
    print("Merge Factors")
    df = merge_data(size_df, quality_df, value_df, yield_df, volatility_df, liquidity_df, growth_df, momentum_df)
    print("Normalize Variables")
    df = normalize_data(df)
    
    print("Fit Regression")
    fit, summary_tbl = factor_significance_check(df)
    
    return fit, summary_tbl