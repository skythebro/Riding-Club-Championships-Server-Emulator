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

- Horse stats. Each one is 0, with +1 while the horse is clean.
- A second game window on this server.
- Finishing a showdown ride.
- The showdown jacket, apple slice, and lucky leaf.
- Energy coming back while you stand in the paddock.
- The 200-day login gifts.
- Upgrading an item to level 6.
- Paying for an extra course slot, or for a jump you place.

## Left as errors on purpose

Steam checkout, Kickstarter, and debug Exec and Eval. A checkout would charge a real wallet, the Kickstarter campaign is over, and debug commands are not run.
