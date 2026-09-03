export const TEAM_SORTS = [
  ["points_per_game", "场均得分", "desc"],
  ["total_points", "总得分", "desc"],
  ["points_against_per_game", "场均失分", "asc"],
  ["point_difference_per_game", "场均净胜", "desc"],
  ["win_percentage", "胜率", "desc"],
  ["wins", "胜场", "desc"],
] as const;

export const PLAYER_SORTS = [
  ["points_per_game", "场均得分", "desc"],
  ["total_points", "总得分", "desc"],
  ["one_point_events", "罚球", "desc"],
  ["two_point_events", "两分球", "desc"],
  ["three_point_events", "三分球", "desc"],
] as const;
