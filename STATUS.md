# EMu checklist

27 September 2026. Prices and payouts live in [`balance.json`](balance.json). Change a number there and restart the server and the card host. Another owner can set their own economy without editing code.

A note in that file says whether a number is the original game or a server choice. The original help pages never published a coin price for an extra course slot, for submitting a course, or for one placed jump, so those stay **0** until you set them.

The server that is running was started with this code. Shop clothes, the starter outfit, the 98/220 action-point bar, and the course-editor maps come from the server, not from a one-off edit in the client.

## This account

Skye, horse Mist. 565 coins, rating 1045, ladder place 1, action points 98 / 220, grooming clean. The extra 20 on the bar are the plain boots and breeches. That includes one ranked win that was paid before the faults-then-time rule. Do not take it back.

Showdown asks for horse level 10 and 150 skill tickets, which is the original rule. This horse is level 1, so that screen will say no until the level is higher, or until `showdown.min_level` and `showdown.skill_tickets` in `balance.json` are lowered for a test.

## Played on the Steam client

- [x] Login, cards, menu, horse, coins, action points, experience
- [x] Avatar save. The horse stayed Mist. A later change asks for 4000 coins. This account cannot pay that
- [x] Plain starter kit. An empty account is given a helmet, jacket, breeches, boots, bridle, saddle, blanket, and ankle boots, and they are put on. The mane uses the first hair color. Change the list in `balance.json` under `starter`
- [x] Shop. Treats plus the full wardrobe, 96 clothes and tack pieces, from `gamedata/wardrobe.json`. A new item with no description crashes the item panel, so every shop card has one
- [x] Club list and Meadow Riders, with the description and both members
- [x] Course list for Meadow, Meadow 2, and Meadow 3
- [x] Paddock, practice, tutorial, grooming screen, daily login sheet
- [x] Course editor. Paddock, winter, desert, stadium, and the tutorial map all load. The tutorial and desert scene names are `Tutorialscene` and `Desertscene`
- [x] Showdown menu. The day’s board is empty because no showdown ride is stored
- [x] Ranked search, a ride against the saved local rival, and the result
- [x] Chat, ladders, profiles, and course likes get an answer
- [ ] A second game window. The other account so far is a socket login, not another `rcc.exe`

## Rules the running server uses

| Rule | Original game | This server |
| --- | --- | --- |
| Play-alone and ranked action points | 20, or 10 with Pro Rider | 20 |
| Energy bar | 200, up to 300 with boots and breeches | 200, plus 10 per level from boots and breeches. Live bar is 220. Stored points are 98 |
| Paddock and friend matches | No action points. The paddock refills energy. The refill speed was not published | 0, and it does not refill |
| Ranked winner | Fewest faults, then the faster time. A quit pays nothing | That rule, plus 25 coins and +15 rating on a win |
| Showdown entry | Level 10, 150 skill tickets, no action points | Same |
| Showdown prizes | 2000 / 500 / 500 / 250 / 100 coins, plus a jacket, apples, or a lucky leaf | Those coins. 2nd place can grant `item_apple_big` and 3rd can grant `item_apple` |
| Course slot, submit, and each placed piece | A cost exists. The amount was never published | 0. Set `course_editor` to charge. The first slot stays free |
| Clean or dirty horse | +1 when clean, −1 when fully dirty | Same, from the grooming thresholds |
| Instant groom | Coins, amount not published. Pro Rider’s stable hand is free | 20 for a fully dirty horse, scaled by dirt. Pro path is free |
| Change horse or rider | 4000 coins, only when a change is saved. The first look is free | 4000 |
| Club | 20 members. Name 30, short name 10, description 400, welcome 100 | Same. Rename stays 0 |
| Practice payout | Not published | 25 coins, 10 horse XP, 20 action points, a little dirt |
| Daily gift | 200 days. The table is in `Rewards for all the daily logins.txt` | A 7-day sheet, 10 through 40 coins. The 200-day list is not applied, because several prize items have no id |
| Carrot, clover, coin bag | 10 / 15 / 25 coins | Same. Use gives 5 XP, 8 XP, or 50 coins |

Shop clothes and tack use the price in `gamedata/wardrobe.json`. Copy that file into the game's `UserData` folder. It is the catalog, not `balance.json`.

## Added for local testing

`agent_mcp/server.py` can list buttons, click one, and change scenes while the game is running. The MelonLoader side is `client_bridge/RccAgentBridge.cs`. See the README.

- [x] `game_buttons`, `game_click`, and `game_scene`
- [x] Opening the course editor and loading `terrain:tutorial_terrain` and `terrain:desert_terrain` from those tools

## Still open

- [ ] Publish a price for an extra show-jumping slot, an agility slot, and one placed jump. Nothing public gives the number
- [ ] Showdown items that are still missing. The exclusive jacket and the apple slice have no item id in the client. The lucky leaf id is `item_lucky_leaf`, but its icon is not in the asset list
- [ ] A finished showdown ride stored on the day's board
- [ ] Refill action points in the paddock. The help page says it happens. It does not say how fast
- [ ] Apply the 200-day login list once the missing prize items exist
- [ ] Two Steam clients on this server at once
- [ ] Item upgrades to level 6. The original upgrade price was not published, so an upgrade does not spend coins or raise the level yet

## Left as failures

These stay failures on purpose. The reason is in the log.

- [x] Steam checkout. A success would charge a real wallet
- [x] Kickstarter. The campaign is over and there is no pledge API
- [x] Debug Exec and Eval. They are not run
