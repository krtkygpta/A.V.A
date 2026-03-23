# import time
# from messages import message_list, checkevent
# # import threading
# # import json
# import re
# import threading
# import time
# import json

# def threadoftimer(timer_length, tool_id):
#     # timer_length = re.sub("/D", "", timer_length)
#     time.sleep(int(timer_length))
#     message_list.append({'role': 'tool', 'content': f'Timer for {timer_length} completed', 'tool_call_id': tool_id})  # Simulating the message_list update
#     # Simulating setting an event
#     # print("timer up")
#     checkevent.set()  # Uncomment if using an actual threading event

# def timer(timer_length, tool_id):
#     thread1090 = threading.Thread(target=threadoftimer, args=(timer_length, tool_id))
#     thread1090.start()
#     # print("HIIIIII")
#     return json.dumps({'status': 'started', 'message': f'please wait for the timer to finish'})

# # Example usage
# # print(timer(5, 'tool_123'))
