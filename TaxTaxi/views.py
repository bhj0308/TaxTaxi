from django.shortcuts import render
from .forms import ShipmentForm


def shipment_calculator(request):
    result = None
    
    if request.method == "POST":
        form = ShipmentForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            
            # Simple calculation (PyTorch/RAG later)
            base_rates = {"dhl": 12, "fedex": 10, "ups": 9}
            shipping = float(data['weight_kg']) * base_rates.get(data['carrier'], 10)
            tariff = float(data['item_value']) * 0.12  # 12% placeholder
            total = shipping + tariff
            
            result = {
                'carrier': data['carrier'].upper(),
                'shipping': round(shipping, 2),
                'tariff': round(tariff, 2), 
                'total': round(total, 2),
                'item': data['item_name']
            }
    else:
        form = ShipmentForm()
    
    return render(request, 'calculator.html', {'form': form, 'result': result})
