export const TEAM_SORTS = [
  ["points_per_game", "场均得分"],
  ["total_points", "总得分"],
  ["points_against_per_game", "场均失分"],
  ["point_difference_per_game", "场均净胜"],
  ["win_percentage", "胜率"],
  ["wins", "胜场"],
] as const;

export const PLAYER_SORTS = [
  ["points_per_game", "场均得分"],
  ["total_points", "总得分"],
  ["one_point_events", "罚球"],
  ["two_point_events", "两分球"],
  ["three_point_events", "三分球"],
  ["fouls_per_game", "场均犯规"],
] as const;
