/*****************************************************************************
 * Credit Card Behavioural Score  (BHV-CC-01)
 * Owner   : Cards Risk Analytics
 * Purpose : Monthly behavioural PD for existing credit card accounts, used for
 *           limit management and collections prioritisation.
 * Note    : SYNTHETIC hackathon example. Library CRDW and its tables do not
 *           exist; this program is inventoried and documented only, never run.
 *****************************************************************************/

libname crdw  '/data/warehouse/cards';
libname model '/models/bhv_cc_01';

%let perf_month = 202312;
%let outcome_window = 12;

/* 1. Build the modelling base: one row per account at the observation month */
data work.base;
  merge crdw.account_month (where=(month_id = &perf_month.) in=a)
        crdw.bureau_monthly (keep=account_id month_id bureau_score num_searches_6m)
        crdw.delinquency_flags (keep=account_id worst_status_12m);
  by account_id;
  if a;

  utilisation = min(balance / max(credit_limit, 1), 1.5);
  payment_ratio = min(payment_amt / max(min_payment_due, 1), 10);
  months_on_book = intck('month', open_date, input(put(&perf_month., 6.), yymmn6.));
  if worst_status_12m >= 3 then bad_flag = 1; else bad_flag = 0;
run;

/* 2. Coarse classing of continuous characteristics */
proc format;
  value util_fmt   low -< 0.3 = '1: <30%'   0.3 -< 0.7 = '2: 30-70%'
                   0.7 -< 0.9 = '3: 70-90%' 0.9 - high = '4: 90%+';
  value mob_fmt    low -< 12  = '1: <1y'    12 -< 36   = '2: 1-3y'
                   36 - high  = '3: 3y+';
run;

data work.base_binned;
  set work.base;
  util_band = put(utilisation, util_fmt.);
  mob_band  = put(months_on_book, mob_fmt.);
run;

/* 3. Logistic regression with stepwise selection */
proc logistic data=work.base_binned descending outmodel=model.bhv_cc_01_fit;
  class util_band mob_band / param=ref;
  model bad_flag = util_band mob_band payment_ratio bureau_score num_searches_6m
        / selection=stepwise slentry=0.05 slstay=0.05;
  score data=work.base_binned out=model.bhv_cc_01_scored;
run;

/* 4. Export scores for the limit-management engine */
proc export data=model.bhv_cc_01_scored (keep=account_id p_1)
  outfile='/exports/bhv_cc_01_scores.csv' dbms=csv replace;
run;
