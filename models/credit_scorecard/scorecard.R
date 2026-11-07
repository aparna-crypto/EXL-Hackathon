# =============================================================================
# Retail Application Scorecard  (RCS-APP-02)
# Owner      : Retail Credit Risk Modelling
# Purpose    : Probability of default (12m) for unsecured personal loan applications
# Developed  : 2019   Last validated: 2021-03-15
# Data       : data/processed/german_credit_{train,test}.csv  (public UCI German Credit,
#              used here as a stand-in for the bank's application data)
# =============================================================================
# Method: weight-of-evidence (WoE) binning + logistic regression, scaled to points.
#   1. Numeric variables with more than 10 distinct values are cut into 5 quantile bins
#      (quantile type 7, breaks from the training data, outer breaks set to -Inf/Inf).
#      All other variables are treated as categorical, one bin per level.
#   2. WoE per bin = ln( ((n_good + 0.5) / total_good) / ((n_bad + 0.5) / total_bad) ).
#      Information value IV = sum((dist_good - dist_bad) * WoE) with the same smoothing.
#   3. Variables with IV >= 0.02 are kept. Protected attributes are never candidates.
#   4. Logistic regression of bad (1 = default) on the WoE-transformed variables.
#   5. Score = offset + factor * ln(odds_good), PDO 20, 600 points at good:bad odds 50:1.
# Usage: Rscript models/credit_scorecard/scorecard.R [data_dir] [out_dir]
# =============================================================================

suppressPackageStartupMessages(library(jsonlite))
set.seed(42)

args     <- commandArgs(trailingOnly = TRUE)
data_dir <- if (length(args) >= 1) args[1] else "data/processed"
out_dir  <- if (length(args) >= 2) args[2] else "artifacts/reference/credit_scorecard"
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

train <- read.csv(file.path(data_dir, "german_credit_train.csv"), stringsAsFactors = FALSE)
test  <- read.csv(file.path(data_dir, "german_credit_test.csv"),  stringsAsFactors = FALSE)

TARGET      <- "bad"
EXCLUDED    <- c("id", TARGET, "personal_status_sex", "sex", "age_group", "foreign_worker")
CANDIDATES  <- setdiff(names(train), EXCLUDED)
N_BINS      <- 5
IV_MIN      <- 0.02
PDO         <- 20
BASE_SCORE  <- 600
BASE_ODDS   <- 50

# ---- binning -------------------------------------------------------------------------------
is_binned_numeric <- function(x) is.numeric(x) && length(unique(x)) > 10

numeric_breaks <- function(x) {
  br <- unique(quantile(x, probs = seq(0, 1, length.out = N_BINS + 1), type = 7, names = FALSE))
  br[1] <- -Inf
  br[length(br)] <- Inf
  br
}

assign_bin <- function(x, breaks) {
  if (is.null(breaks)) return(as.character(x))
  as.character(cut(x, breaks = breaks, right = TRUE, include.lowest = TRUE, dig.lab = 10))
}

# ---- WoE / IV ------------------------------------------------------------------------------
woe_table <- function(bin, y) {
  total_good <- sum(y == 0)
  total_bad  <- sum(y == 1)
  levels <- sort(unique(bin))
  n_good <- sapply(levels, function(b) sum(bin == b & y == 0))
  n_bad  <- sapply(levels, function(b) sum(bin == b & y == 1))
  dist_good <- (n_good + 0.5) / total_good
  dist_bad  <- (n_bad + 0.5) / total_bad
  woe <- log(dist_good / dist_bad)
  data.frame(bin = levels, n_good = n_good, n_bad = n_bad, woe = woe,
             iv_part = (dist_good - dist_bad) * woe, stringsAsFactors = FALSE)
}

apply_woe <- function(bin, table) {
  w <- table$woe[match(bin, table$bin)]
  w[is.na(w)] <- 0  # level not seen in training: neutral WoE
  w
}

breaks <- list()
tables <- list()
iv     <- c()
for (v in CANDIDATES) {
  breaks[[v]] <- if (is_binned_numeric(train[[v]])) numeric_breaks(train[[v]]) else NULL
  tables[[v]] <- woe_table(assign_bin(train[[v]], breaks[[v]]), train[[TARGET]])
  iv[v] <- sum(tables[[v]]$iv_part)
}
selected <- names(iv)[iv >= IV_MIN]

woe_frame <- function(df) {
  out <- data.frame(row.names = seq_len(nrow(df)))
  for (v in selected) out[[paste0("woe_", v)]] <- apply_woe(assign_bin(df[[v]], breaks[[v]]), tables[[v]])
  out
}

# ---- model ---------------------------------------------------------------------------------
X_train <- woe_frame(train)
X_test  <- woe_frame(test)
fit <- glm(train[[TARGET]] ~ ., data = X_train, family = binomial())

factor_pts <- PDO / log(2)
offset_pts <- BASE_SCORE - factor_pts * log(BASE_ODDS)
to_score   <- function(pd) offset_pts + factor_pts * log((1 - pd) / pd)

pd_train <- as.numeric(predict(fit, newdata = X_train, type = "response"))
pd_test  <- as.numeric(predict(fit, newdata = X_test,  type = "response"))

# ---- metrics (computed here so the original's own numbers are on record) ----------------------
auc <- function(y, p) {
  r <- rank(p)
  n1 <- sum(y == 1); n0 <- sum(y == 0)
  (sum(r[y == 1]) - n1 * (n1 + 1) / 2) / (n1 * n0)
}
ks <- function(y, p) {
  thresholds <- sort(unique(p))
  max(sapply(thresholds, function(t) abs(mean(p[y == 1] <= t) - mean(p[y == 0] <= t))))
}
metrics <- list()
for (s in c("train", "test")) {
  y <- if (s == "train") train[[TARGET]] else test[[TARGET]]
  p <- if (s == "train") pd_train else pd_test
  a <- auc(y, p)
  metrics[[s]] <- list(n = length(y), bad_rate = mean(y), auc = a, gini = 2 * a - 1, ks = ks(y, p))
}

# ---- outputs -------------------------------------------------------------------------------
bins_out <- do.call(rbind, lapply(CANDIDATES, function(v) {
  t <- tables[[v]]
  data.frame(variable = v, bin = t$bin, n_good = t$n_good, n_bad = t$n_bad, woe = t$woe,
             iv_variable = iv[[v]], selected = v %in% selected, stringsAsFactors = FALSE)
}))
write.csv(bins_out, file.path(out_dir, "bins.csv"), row.names = FALSE)

co <- summary(fit)$coefficients
write.csv(data.frame(term = rownames(co), estimate = co[, 1], std_error = co[, 2],
                     row.names = NULL), file.path(out_dir, "coefficients.csv"), row.names = FALSE)

write.csv(data.frame(id = test$id, bad = test[[TARGET]], pd = pd_test, score = to_score(pd_test)),
          file.path(out_dir, "predictions_test.csv"), row.names = FALSE)

writeLines(toJSON(list(
  model = "credit_scorecard", language = "R", r_version = as.character(getRversion()),
  seed = 42, n_bins = N_BINS, iv_min = IV_MIN, pdo = PDO, base_score = BASE_SCORE,
  base_odds = BASE_ODDS, selected_variables = selected,
  breaks = lapply(breaks[!vapply(breaks, is.null, logical(1))], function(b) b),
  metrics = metrics
), auto_unbox = TRUE, digits = NA, pretty = TRUE), file.path(out_dir, "metrics.json"))

cat(sprintf("credit_scorecard: %d variables selected; test AUC %.4f, Gini %.4f, KS %.4f\n",
            length(selected), metrics$test$auc, metrics$test$gini, metrics$test$ks))
