from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import SubscriptionPlan, Subscription
from django.utils import timezone
from datetime import timedelta


@login_required
def subscription_plans(request):
    plans = SubscriptionPlan.objects.all()
    user_subscription = Subscription.objects.filter(user=request.user).first()

    for plan in plans:
        plan.features_list = plan.features.split(',')

    context = {
        'plans': plans,
        'user_subscription': user_subscription,
    }
    return render(request, 'subscription/plans.html', context)


@login_required
def subscribe(request, plan_id):
    plan = SubscriptionPlan.objects.get(id=plan_id)
    user_subscription = Subscription.objects.filter(user=request.user).first()

    if user_subscription:
        user_subscription.plan = plan
        user_subscription.start_date = timezone.now()
        user_subscription.end_date = timezone.now() + timedelta(days=plan.duration_days)
        user_subscription.save()
    else:
        Subscription.objects.create(
            user=request.user,
            plan=plan,
            end_date=timezone.now() + timedelta(days=plan.duration_days)
        )

    return redirect('subscription_plans')
