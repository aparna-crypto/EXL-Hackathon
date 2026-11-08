# R oracle for transformation unit tests. Loads the function definitions and constants from a
# legacy R script WITHOUT running its data steps, evaluates each test case and writes the results.
# Usage: Rscript r/oracle.R <legacy.R> <cases.json> <results.json>
suppressPackageStartupMessages(library(jsonlite))
args <- commandArgs(trailingOnly = TRUE)
data_symbols <- c("train", "test", "args", "fit", "X_train", "X_test", "pd_train", "pd_test",
                  "breaks", "tables", "iv", "selected", "metrics", "co", "bins_out",
                  "data_dir", "out_dir")
env <- new.env()
for (e in parse(args[1])) {
  if (!is.call(e) || !identical(e[[1]], as.name("<-"))) next
  rhs <- e[[3]]
  is_fun <- is.call(rhs) && identical(rhs[[1]], as.name("function"))
  if (is_fun || !any(all.vars(rhs) %in% data_symbols)) eval(e, env)
}
num <- function(x) if (is.character(x)) as.numeric(x) else x
cases <- fromJSON(args[2], simplifyVector = FALSE)
vec <- function(v) {
  if (is.null(v)) return(NULL)
  if (length(v) > 0 && is.list(v[[1]]))  # list of records -> data.frame
    return(fromJSON(toJSON(v, auto_unbox = TRUE, digits = NA), simplifyDataFrame = TRUE))
  unlist(v)
}
out <- lapply(cases, function(case) {
  a <- lapply(case$args, vec)
  if (!is.null(a$breaks)) a$breaks <- num(a$breaks)
  res <- tryCatch(list(result = do.call(get(case$fn, envir = env), a)),
                  error = function(err) list(error = conditionMessage(err)))
  c(list(id = case$id), res)
})
writeLines(toJSON(out, auto_unbox = TRUE, digits = NA, na = "string", dataframe = "rows",
                  null = "null"), args[3])
