from django import forms

CARRIER_CHOICES = [
    ("dhl", "DHL"),
    ("fedex", "FedEx"), 
    ("ups", "UPS"),
]

class ShipmentForm(forms.Form):
    item_name = forms.CharField(label="Item name", max_length=100)
    item_value = forms.DecimalField(label="Item value (USD)", min_value=0, initial=100)
    weight_kg = forms.DecimalField(label="Weight (kg)", min_value=0, initial=1.0)
    origin_country = forms.CharField(label="From (country code)", max_length=2, initial="US")
    destination_country = forms.CharField(label="To (country code)", max_length=2, initial="CA")
    carrier = forms.ChoiceField(label="Carrier", choices=CARRIER_CHOICES, initial="dhl")
