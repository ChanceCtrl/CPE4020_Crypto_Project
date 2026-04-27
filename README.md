# CPE4020_Crypto_Project

mrow

-- TO RUN THE DEMO --

Validator and Alternate Validator need to have their
'known_validator' fields filled out with their respective
IP addresses. Be sure that the validators can access the
key files (walletX.pub/priv). They are expected to be in the 
same directory as the python file itself.

The keys are already generated and availiable, but the keygen
and extract_key_for_arduino python scripts can be used in order to
generate new keys and load them onto the Arduino (see files for
further instruction)

The Arduino expects to receive ADC values for the current (Pin A2) and 
voltage (Pin A1) of the charging session. Additionally, the Arduino 
needs to be given an IP addresses and port of the validator it will 
publish data to. 

Finally, the dashboards can be used by inputting the validator(s) IP address
and pressing the 'LIVE' button. This will surface the data from the validators
end points.