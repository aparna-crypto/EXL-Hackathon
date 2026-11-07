# Environment check used by tests/test_r_env.py: prints R version and a JSON-encoded GLM fit.
suppressPackageStartupMessages(library(jsonlite))
set.seed(42)
x <- rnorm(200)
y <- rbinom(200, 1, plogis(0.5 + 1.2 * x))
fit <- glm(y ~ x, family = binomial())
cat(toJSON(list(r_version = as.character(getRversion()),
                coef = unname(coef(fit))), digits = 10, auto_unbox = TRUE))
