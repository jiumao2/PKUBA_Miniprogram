import type {
  PreviewSeasonConfiguration,
  SeasonConfiguration,
} from "@pkuba/api-client";

export function buildSeasonConfigurationPayload(
  configuration: SeasonConfiguration,
): PreviewSeasonConfiguration {
  const divisionIds = new Set(
    configuration.divisions.map((row) => row.id).filter(Boolean),
  );
  return {
    expected_version: configuration.version,
    name: configuration.name,
    competition_type: configuration.competition_type,
    year: configuration.year,
    starts_on: configuration.starts_on,
    ends_on: configuration.ends_on,
    divisions: configuration.divisions.map((row) => ({
      id: row.id || null,
      name: row.name,
      gender: row.gender,
    })),
    venues: configuration.venues.map((row) => ({
      id: row.id || null,
      name: row.name,
      active: row.active,
    })),
    periods: configuration.periods.map((row) => ({
      id: row.id || null,
      name: row.name,
      start_time: row.start_time,
      default_capacities: row.default_capacities,
    })),
    slot_families: configuration.slot_families
      .filter((row) => divisionIds.has(row.division_id))
      .map((row) => ({
        id: row.id || null,
        division_id: row.division_id,
        stage: row.stage,
        round_number: row.round_number,
        prefix: row.prefix,
        slot_count: row.slot_count,
      })),
    date_capacity_overrides: configuration.date_capacity_overrides.map((row) => ({
      date: row.date,
      period_code: row.period_code.toLowerCase(),
      capacity: row.capacity,
      note: row.note,
    })),
  };
}
