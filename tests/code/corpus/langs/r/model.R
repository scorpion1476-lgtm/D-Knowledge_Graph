library(stats)
library(dplyr)

fit_model <- function(data) {
  summary(data)
}

predict_values <- function(model, newdata) {
  fit_model(newdata)
}

setClass("Account", representation(balance = "numeric"))

setMethod("show", "Account", function(object) {
  print(object)
})
