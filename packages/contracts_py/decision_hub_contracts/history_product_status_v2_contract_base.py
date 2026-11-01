from pydantic import BaseModel, ConfigDict, model_validator


class HistoryProductContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="after")
    def honest_status(self):
        if type(self).__name__ == "HistoryForecastStatusV2" and (
            (self.state == "available_unqualified") != (self.probabilities is not None)
        ):
            raise ValueError("history_forecast_probability_state_invalid")
        if getattr(self, "schema_version", None) == "history-product-status.v2":
            if self.data.current_originals_verified is False and (
                self.actions.can_fit or self.actions.can_forecast
            ):
                raise ValueError("history_status_fast_metadata_cannot_authorize_fit_or_forecast")
            if (
                self.forecast.state != "available_unqualified"
                and self.forecast.probabilities is not None
            ):
                raise ValueError("history_status_unavailable_probability_must_be_null")
            if (
                self.forecast.state == "available_unqualified"
                and self.forecast.probabilities is None
            ):
                raise ValueError("history_status_available_probability_missing")
        if getattr(self, "schema_version", None) == "history-asset-refresh-reply.v2":
            if self.outcome == "committed_verified":
                if (
                    self.committed is not True
                    or self.forecast_id is None
                    or self.cutoff_at is None
                    or tuple(item.threshold for item in self.frequencies) != ("0.02", "0.05")
                ):
                    raise ValueError("history_refresh_verified_identity_or_frequencies_missing")
            elif self.frequencies:
                raise ValueError("history_refresh_unconfirmed_cannot_present_probability")
            if self.outcome == "committed_readback_unconfirmed" and self.committed is not True:
                raise ValueError("history_refresh_known_commit_required")
            if self.outcome == "unconfirmed" and self.committed is not None:
                raise ValueError("history_refresh_unknown_commit_must_be_null")
        return self
