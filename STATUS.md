# EMu checklist

27 September 2026. Numbers live in [`balance.json`](balance.json). Change one, save, and restart the server and the card host.

The test account is the rider Skye and the horse Mist. Skye has 565 coins, rating 1045, ladder place 1, and 98 / 220 action points. Mist is clean and level 1. The extra 20 on the bar are the plain boots and breeches. Leave the coins and the rating as they are.

Showdown wants horse level 10 and 150 skill tickets, so that screen says no until Mist levels up, or until those two numbers in `balance.json` are lowered.

## Tested

- Login, cards, the menu, the horse, coins, action points, and experience.
- Saving the avatar. The horse kept the name Mist.
- A starter outfit on a new account: helmet, jacket, breeches, boots, bridle, saddle, blanket, and ankle boots, already worn. The mane is the first hair color.
- The shop: carrot, clover, coin bag, and the full wardrobe, 96 clothes and tack pieces.
- Meadow Riders, the club search, and both members.
- Courses Meadow, Meadow 2, and Meadow 3.
- Paddock, practice, the tutorial, the grooming screen, and the daily login sheet.
- The course editor: paddock, winter, desert, stadium, and the tutorial map.
- The showdown menu.
- Ranked search, a round against the saved local rival, and the result screen.
- Chat, ladders, profiles, and course likes.

## What it costs

Play alone and ranked each take 20 action points. The bar holds 200, and boots and breeches add more, 10 per level up to 50. Skye's bar is 220. Paddock and friend matches are free.

A ranked round is won by fewer faults, then the faster time. Quitting pays nothing. A win pays 25 coins and 15 rating. A loss pays nothing.

A clean horse is +1 on every stat. A fully dirty horse is −1. Grooming a fully dirty horse costs 20 coins, less if it is only a bit dirty. A Pro Rider stable hand is free. A practice ride pays 25 coins and 10 horse XP, spends 20 action points, and dirties the horse a little.

The first course slot is free. Extra slots, submitting a course, and each placed piece cost 0 until you set `course_editor` in `balance.json`.

Changing a saved avatar costs 4000 coins, which is what the original game charged. The first time you make one is free. Set `avatar_change_coins` in `balance.json` if you want a different price.

A club holds 20 members. The name is 30 characters, the short name 10, the description 400, and the welcome text 100. Renaming is free.

The daily gift is a 7-day sheet, 10 through 40 coins. The original 200-day list is in `Rewards for all the daily logins.txt` and is not in use yet.

Carrot is 10 coins and 5 XP. Clover is 15 coins and 8 XP. The coin bag is 25 coins and gives 50 coins back.

Clothes and tack prices are in `gamedata/wardrobe.json`.

Showdown pays 2000, then 500, 500, 250, and 100 coins. Second place also gets a big apple. Third gets an apple.

## Still to test

- Horse stats. They start at `horse_stats.start` in `balance.json`, 0 by default. A level gives one skill point to spend, up to 10 on a stat, and a clean coat still adds +1. The stats panel should show all six with the coat buff, and the buff should go away once the coat is no longer clean. The stats change the ride: the client scales speed, timing, acceleration, stamina, and jumps by the stat out of 25. The original XP table for all 60 levels was not recovered, so a level is 100 XP until that table shows up.
- A practice ride that beats your faults, then your time, on that course. The result screen should say it is a new personal record. Quitting should pay nothing and leave the horse and the course count alone. The Friends tab on that course should list only you and your Steam friends.
- A finished ranked round dirtying the horse the same small amount as practice. Quitting should leave the coat alone. Against another rider, the result screen should name that rider.
- Finishing a friend challenge. Both riders should see the time and faults, and who won. It should not pay coins. After both results are in, Send Challenge should show again.
- A second game window on this server. Opening that rider should show the clothes and tack they have on. Ignoring them in chat should flip the button and keep them on the ignore list after a relog. The Friends ladder should list only you and friends who play here, and the Club Members ladder only your club, each with their global place. The global ladder should stop at the top count the menu asks for. Asking for particular riders should list only those riders, still with that global place.
- Finishing a showdown ride. The menu should show the real weekday, Sunday should cost nothing, the ride button should stay hidden after one finish that day, and that finish should dirty the horse the same small amount as practice. Monday's board should not list a Sunday finish. The arrows on the day board and the qualifying list should move ten places at a time, and your own row should stay at the bottom.
- The showdown jacket, apple slice, and lucky leaf.
- Energy coming back while you stand in the paddock.
- The daily gift opening by itself when it has not been taken today, and the menu timer counting down to midnight.
- The 200-day login gifts.
- Upgrading an item to level 6.
- Paying for an extra course slot, or for a jump you place. Submitting a course should publish the slot you chose, under the name you saved, with that map's terrain, jump height, and ideal time. It still costs nothing.
- Play Alone search. Typing part of a course name should list only those courses. My courses should list only the ones you own. Friends should list only courses your Steam friends own. New should list the newest course first.
- Liking a course. The number on the heart should be how many people like that course. Unliking should drop that number by one.
- Finishing the paddock tutorial. The next login, if the horse is not saved yet, should open horse creation again. An account already in the menu should stay there.
- Inviting someone to the club. Their requests list should show that club, the badge should count it, and it should still be there after a relog. Declining or cancelling should take it off the list. The top club list should stop at the page the menu asks for.
- Leaving a club. The people who stay should see that rider gone, and the news should say they left. Kicking someone should say they were removed. Changing their role should say a member role changed.
- Opening a rider. The friend count should be how many of their Steam friends already play on this server.
- The number at the top of the menu. It should be how many riders are connected, and it should change when someone joins or leaves.
- Buying a course slot on the Agility tab. That slot should show there, not on show jumping. The first slot is still free.
- Opening an agility course. It should load the saved agility map, not a show-jumping meadow.
- A name in chat. It should be the rider's name. The horse's name should not show up as a picture address.
- The chat window. Global chat should already be there when you reach the menu, before anyone has typed.

- Club roles. A member should not be able to rename, kick, or promote. A manager can invite and kick plain members. Making someone owner should make you a manager. When the owner leaves, a manager or the next member should become owner. A full club should refuse a 21st rider with the club-is-full message.
- Two riders on this server in one ranked round. Each result screen should name the other rider and their real time. Only the winner should be paid. Closing one game mid-ride should give the other rider the win.
- Wearing gear. Only owned clothes and tack, plus the starter pieces, should stay on.
- Saving a course into a slot you did not buy should fail.

- Chat moderation in the chat window. A mute should print how long and why, and the muted rider's earlier lines should disappear for everyone. Unmuting should print that the mute ended. A rider in `admin_steam_ids` should show the admin marker in chat. The connection stays up through all of this (tested); the chat text itself has not been looked at yet.
- Chat times. Each line should show today's time, not a date in 1970.
- The news panel showing `news` from `balance.json`.

## Left as errors on purpose

Steam checkout, Kickstarter, and debug Exec and Eval. A checkout would charge a real wallet, the Kickstarter campaign is over, and debug commands are not run.
