# Install any missing R packages listed in r/packages.txt and record versions.
pkgs <- trimws(readLines("r/packages.txt"))
pkgs <- pkgs[pkgs != "" & !startsWith(pkgs, "#")]
missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing) > 0) {
  install.packages(missing, repos = "https://cloud.r-project.org")
}
versions <- vapply(pkgs, function(p) as.character(packageVersion(p)), character(1))
writeLines(c(paste("R", getRversion()), paste(pkgs, versions)), "r/package_versions.txt")
cat("R", as.character(getRversion()), "| packages:", paste(pkgs, versions), "\n")
