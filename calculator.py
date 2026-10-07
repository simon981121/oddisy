def calculate_fair_odds(outcomes):
    # 1. Räkna ut implied probability för varje utfall
    implied_probs = []
    for outcome in outcomes: 
        prob = 1 / outcome["price"]
        implied_probs.append(prob)


    #2 upphöjer varje odds med "k" som vi räknar from i find_k
    true_probs = []
    k = find_k(implied_probs)
    for implied_prob in implied_probs: 
        true_prob = implied_prob ** k 
        true_probs.append(true_prob)

    # 4. Räkna ut rättvisa odds (1 / sann sannolikhet)
    all_true_odds = []
    for true_prob in true_probs:
        true_odd = 1 / true_prob
        all_true_odds.append(true_odd)

    # 5. Returnera resultatet
    return all_true_odds


def find_k(implied_probs): 
    low = 1.0
    high = 10.0

    for _ in range(100):
        powerd_probs = []
        mid = (low + high) / 2
        for p in implied_probs: 
            probs = p ** mid
            powerd_probs.append(probs)

        summan = sum(powerd_probs)
        if summan > 1:
            low = mid
        else: 
            high = mid
        
    return mid


def find_pinnacle(bookmakers):
    for bookmaker in bookmakers:
        if bookmaker["key"] == "pinnacle":
            return bookmaker
    return None